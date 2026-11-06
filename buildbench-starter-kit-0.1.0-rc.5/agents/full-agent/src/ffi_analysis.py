"""Bounded Rust C-char interface evidence and frozen repair obligations.

No compiler, dependency download, architecture table, or package lookup is used.
The syntax/flow readers support a documented subset, not full Rust semantics.
"""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from time import monotonic

from .ffi_rust import CSTR_KEY, CSTR_DOC, Resolver, parse_file
from .ffi_flows import link_functions, slot_type

MAX_FILES = 300
MAX_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_OBLIGATIONS = 200
METHOD = 'ffi-contracts-v2'


def enabled():
    value = os.environ.get('BB_FFI_ANALYSIS', '0')
    if value not in {'0', '1'}:
        raise ValueError('BB_FFI_ANALYSIS must be 0 or 1')
    return value == '1'


@dataclass
class Index:
    functions: dict = field(default_factory=dict)
    edges: set = field(default_factory=set)
    unknown: list = field(default_factory=list)
    hashes: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)
    type_evidence: list = field(default_factory=list)


def index_sources(root, overlay=None):
    """Read bounded source; overlay contains simulated files, never disk writes."""
    index = Index()
    base = Path(root) / 'input'
    if base.is_symlink() or not base.is_dir():
        index.unknown.append('input is missing or not a real directory')
        return index
    total = count = entries = 0
    deadline = monotonic() + 3
    for directory, dirs, files in os.walk(base, followlinks=False):
        entries += len(dirs) + len(files)
        if entries > 10000 or monotonic() > deadline:
            index.unknown.append('directory/time scan limit reached')
            return index
        dirs[:] = sorted(d for d in dirs if d not in {'.git', '.pc', 'target', 'vendor'}
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if not name.endswith('.rs'):
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            count += 1
            if count > MAX_FILES or total >= MAX_BYTES:
                index.unknown.append('source scan limit reached')
                return index
            try:
                if path.is_symlink() or not path.is_file():
                    raise ValueError('nonregular source')
                if overlay is not None and relative in overlay:
                    data = overlay[relative].encode('utf-8')
                else:
                    with path.open('rb') as stream:
                        data = stream.read(MAX_FILE_BYTES + 1)
                total += len(data)
                if len(data) > MAX_FILE_BYTES or total > MAX_BYTES:
                    raise ValueError('source byte limit')
                index.hashes[relative] = hashlib.sha256(data).hexdigest()
                unit = parse_file(relative, data.decode('utf-8'))
                index.units[relative] = unit
                index.unknown.extend(unit.unknown)
                for function in unit.functions:
                    index.functions.setdefault(function.key, []).append(function)
            except (OSError, ValueError, UnicodeError) as error:
                index.unknown.append(f'{relative}: {error}')
    resolver = Resolver(index.units)
    for group in index.functions.values():
        for function in group:
            function.params = [(name, resolver.pointer(function.path, raw))
                               for (name, _), raw in zip(function.params, function.raw_params)]
            function.return_type = resolver.pointer(function.path, function.raw_return)
    link_functions(index, resolver)
    index.type_evidence = sorted(resolver.evidence)
    return index


# Retained helper name for the coverage checker; -1 denotes a return slot.
param_type = slot_type


def slot_label(number):
    return 'return value' if number == -1 else f'parameter {number}'


@dataclass
class Analysis:
    original: Index
    obligations: dict
    edges: set

    def report(self, current, label):
        rows, counts = [], {'satisfied': 0, 'remaining': 0, 'unknown': 0}
        for key, expected in sorted(self.obligations.items()):
            actual = param_type(current, key)
            old_function = self.original.functions[key[:2]][0]
            new_functions = current.functions.get(key[:2], [])
            same_role = len(new_functions) == 1 and new_functions[0].foreign == old_function.foreign
            required_edges = {edge for edge in self.edges if edge[0] == key}
            if actual is None or not same_role or not required_edges.issubset(current.edges):
                status = 'unknown'
            elif actual == expected:
                status = 'satisfied'
            elif actual[0] == expected[0] and actual[1] in {'i8', 'u8'}:
                status = 'remaining'
            else:
                status = 'unknown'
            # Changing the foreign declaration cannot satisfy the original contract.
            if old_function.foreign and actual != expected:
                status = 'unknown'
            counts[status] += 1
            rows.append(f'{status}: {key[0]}:{old_function.line} {key[1]} {slot_label(key[2])} '
                        f'requires *{expected[0]} c_char; current={actual}')
        return {
            'label': label, 'analysis_mode': METHOD, 'counts': counts, 'obligations': rows,
            'standard_contracts': [CSTR_DOC] if CSTR_KEY in self.original.functions else [],
            'original_type_evidence': self.original.type_evidence[:80],
            'current_type_evidence': current.type_evidence[:80],
            'relationships': [f'{source[0]}:{self.original.functions[source[:2]][0].line} '
                              f'{source[1]} {slot_label(source[2])} depends on '
                              f'{target[0]}:{self.original.functions[target[:2]][0].line} '
                              f'{target[1]} {slot_label(target[2])}'
                              for source, target in sorted(self.edges)],
            'unknown': sorted(set(self.original.unknown + current.unknown))[:80],
            'original_source_hashes': self.original.hashes,
            'source_hashes': current.hashes,
            'build_validated': False,
        }

    def render(self, current, label):
        report = self.report(current, label)
        return '\n'.join([
            '=== Rust FFI source-contract coverage: ' + label + ' ===',
            'Original declarations anchor obligations; historical errors are not current build results.',
            'Counts: ' + str(report['counts']),
            ('No confirmed C-char parameter contract found: analysis is inconclusive.'
             if not self.obligations else 'Parameter positions are zero-based.'),
            *('Standard-library contract: ' + url for url in report['standard_contracts']),
            *('Current source declaration: ' + line for line in report['current_type_evidence']),
            *report['relationships'],
            *report['obligations'],
            *('UNKNOWN: ' + item for item in report['unknown']),
            'Unknown details and declaration evidence are capped at 80 entries each; '
            'all counts cover only indexed obligations.',
            'Direct parameters, simple pointer returns and explicit aliases/imports are checked. '
            'Generics, cfg, macros, methods and indirect flows may remain unresolved. '
            'Coverage is not a build result.',
            'Repair confirmed remaining obligations together. Preserve unrelated byte buffers, '
            'mutability, foreign declarations and functionality. Do not use global i8/u8 replacement.',
            'Use a project-supported C-char alias. If only unknowns remain, explain them; '
            'do not repeat edits merely because the original failure log is unchanged.',
        ])


def analyze(root):
    original = index_sources(root)
    obligations = {}
    for key, group in original.functions.items():
        if len(group) == 1 and group[0].foreign:
            slots = [(number, typ) for number, (_, typ) in enumerate(group[0].params)]
            if group[0].raw_return or group[0].return_type:
                slots.append((-1, group[0].return_type))
            for number, typ in slots:
                if typ and typ[1] == 'c_char':
                    if len(obligations) >= MAX_OBLIGATIONS:
                        original.unknown.append('foreign obligation limit reached')
                        return Analysis(original, obligations, set())
                    obligations[key + (number,)] = typ
                elif typ is None:
                    original.unknown.append(f'{key}: foreign {slot_label(number)}: type not resolved')
    edges = set()
    changed = True
    while changed:
        changed = False
        for source, target in sorted(original.edges):
            if target not in obligations:
                continue
            typ = param_type(original, source)
            expected = obligations[target]
            if typ is None or typ[0] != expected[0]:
                original.unknown.append(f'pointer mutability unresolved: {source}')
                continue
            edges.add((source, target))
            if source not in obligations:
                if len(obligations) >= MAX_OBLIGATIONS:
                    original.unknown.append('obligation limit reached')
                    return Analysis(original, obligations, edges)
                obligations[source] = expected
                changed = True
    return Analysis(original, obligations, edges)


def main():
    """Offline inspection of an unpacked worktree; no edits or model calls."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worktree', type=Path, required=True,
                        help='Worktree root containing input/ and package source')
    args = parser.parse_args()
    analysis = analyze(args.worktree)
    print(json.dumps(analysis.report(analysis.original, 'offline original source'), indent=2))


if __name__ == '__main__':
    main()
