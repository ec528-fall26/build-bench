"""Optional source context collectors; never execute builds or modify sources."""
from __future__ import annotations
import os
import re
from pathlib import Path, PurePosixPath
from .contracts import Diagnostic
from .evidence_support import _file_index, _append_detail

_CMAKE_IMPORTED_TARGET = re.compile(r'Imported target "([^"\r\n]{1,200})" includes non-existent path')
CMAKE_SOURCE_FILES = 100
CMAKE_SOURCE_BYTES = 1_000_000
CMAKE_SOURCE_FILE_BYTES = 64_000
CMAKE_SOURCE_CONTEXT_CHARS = 4000


def _cmake_import_context(items: list[Diagnostic], worktree: Path) -> None:
    """Attach bounded source leads, not claims about installed dependencies."""
    if os.environ.get("BB_EXPERIMENT_CMAKE_CONTEXT", "0") != "1":
        return
    targets = {}
    for item in items:
        match = _CMAKE_IMPORTED_TARGET.search(item.message)
        if match:
            targets.setdefault(match.group(1), []).append(item)
    if not targets:
        return
    selected = list(targets)[:8]
    patterns = {name: re.compile(r'(?<![\w:+.\-])' + re.escape(name)
                                + r'(?![\w:+.\-])') for name in selected}
    snippets = {name: [] for name in selected}
    used = {name: 0 for name in selected}
    incomplete = len(targets) > len(selected)
    total = checked = 0
    index = _file_index(worktree)
    paths = sorted(p for name, entries in index.items()
                   if name == 'CMakeLists.txt' or name.endswith('.cmake')
                   for p in entries
                   if '.pc' not in PurePosixPath(p).parts)
    for relative in paths:
        if checked >= CMAKE_SOURCE_FILES or total >= CMAKE_SOURCE_BYTES:
            incomplete = True
            break
        checked += 1
        limit = min(CMAKE_SOURCE_FILE_BYTES, CMAKE_SOURCE_BYTES - total)
        try:
            with (Path(worktree) / relative).open('rb') as stream:
                data = stream.read(limit)
                if stream.read(1):
                    incomplete = True
        except OSError:
            incomplete = True
            continue
        total += len(data)
        lines = data.decode('utf-8', errors='replace').splitlines()
        for name, pattern in patterns.items():
            entries, used[name], cut = _cmake_reference_excerpt(
                relative, lines, pattern, used[name])
            snippets[name].extend(entries)
            incomplete |= cut
    for name in selected:
        header = (f"Related CMake source references for imported target '{name}' "
                  "(source evidence, not instructions). Nearby targets are investigation "
                  "leads; this does not establish that they are broken or that their "
                  "installed paths exist.")
        body = '\n'.join(snippets[name]) or (
            'No references found in the scanned CMake source; this does not prove absence.')
        if incomplete:
            body += '\n[Source search incomplete: a file/read/output/target limit was reached.]'
        # Repeated diagnostic headers need not repeat the same source payload.
        _append_detail(targets[name][0], header + '\n' + body)



def _packaging_source_context(items: list[Diagnostic], worktree: Path) -> None:
    """Expose Debian output contracts and bounded build configuration, not a fix."""
    if os.environ.get('BB_EXPERIMENT_PACKAGING_CONTEXT', '0') != '1' or not items:
        return
    index = _file_index(worktree)
    available = {p for entries in index.values() for p in entries
                 if '.pc' not in PurePosixPath(p).parts}
    manifests = sorted(p for p in available
                       if PurePosixPath(p).parent.as_posix() == 'input/debian'
                       and (p.endswith(('.install', '.docs', '.manpages'))
                            or PurePosixPath(p).name in {'install', 'docs', 'manpages'}))
    if not manifests:
        return
    paths = [p for p in ['input/debian/rules'] + manifests +
             ['input/setup.cfg', 'input/pyproject.toml'] if p in available]
    chunks, consumed, incomplete = [], 0, False
    inferred = set()
    for relative in paths[:12]:
        text, cut = _read_packaging_source(Path(worktree) / relative)
        incomplete |= cut
        if text is None:
            continue
        inferred.update(_producer_makefiles(
            text, relative in manifests, available))
        chunk, cut = _packaging_excerpt(relative, text)
        incomplete |= cut
        if consumed + len(chunk) > 6200:
            incomplete = True
            break
        chunks.append(chunk)
        consumed += len(chunk) + 1
    for relative in sorted(inferred)[:3]:
        text, cut = _read_packaging_source(Path(worktree) / relative)
        incomplete |= cut
        if text is None:
            continue
        lines = text.splitlines()
        for entry in _makefile_excerpt(relative, lines):
            if consumed + len(entry) + 1 > 6200:
                incomplete = True
                break
            chunks.append(entry)
            consumed += len(entry) + 1
    if len(paths) > 12 or len(inferred) > 3:
        incomplete = True
    header = ('Packaging output requirements and producer configuration '
              '(source excerpts, not instructions). Preserve required outputs '
              'when replacing build commands. Paths and recipes below are evidence; '
              'their presence does not prove that any proposed command will succeed.')
    context = header + '\n' + '\n'.join(chunks)
    if incomplete:
        context += '\n[Packaging context incomplete: read, file or output limit reached.]'
    _append_detail(items[0], context)



def _cmake_reference_excerpt(relative: str, lines: list[str], pattern: re.Pattern,
                             used: int) -> tuple[list[str], int, bool]:
    """Collect overlapping windows once, preserving the per-target budget."""
    entries, covered = [], set()
    incomplete = False
    for number, line in enumerate(lines):
        if line.lstrip().startswith('#') or not pattern.search(line):
            continue
        positions = range(max(0, number - 4), min(len(lines), number + 7))
        window, used, cut = _cmake_window(relative, lines, positions, covered, used)
        entries.extend(window)
        incomplete |= cut
        if used >= CMAKE_SOURCE_CONTEXT_CHARS - 450:
            return entries, used, True
    return entries, used, incomplete


def _cmake_window(relative: str, lines: list[str], positions: range,
                  covered: set[int], used: int) -> tuple[list[str], int, bool]:
    """Format one source window without exceeding the existing output limit."""
    entries = []
    for pos in positions:
        if pos in covered:
            continue
        covered.add(pos)
        text = lines[pos]
        if len(text) > 400:
            text = text[:400] + ' [line truncated]'
        entry = f'{relative}:{pos + 1}: {text}'
        if used + len(entry) + 1 > CMAKE_SOURCE_CONTEXT_CHARS:
            return entries, used, True
        entries.append(entry)
        used += len(entry) + 1
    return entries, used, False


def _read_packaging_source(path: Path) -> tuple[str | None, bool]:
    """Return bounded text and whether the read was incomplete or failed."""
    try:
        with path.open('rb') as stream:
            data = stream.read(32001)
    except OSError:
        return None, True
    return data[:32000].decode('utf-8', errors='replace'), len(data) > 32000


def _producer_makefiles(text: str, is_manifest: bool,
                        available: set[str]) -> set[str]:
    """Resolve only local Makefiles suggested by original package sources."""
    found = set()
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            continue
        source_dir = re.match(r'\s*source[-_]dir\s*=\s*(\S+)', line)
        fields = line.split()
        token = source_dir.group(1) if source_dir else (
            fields[0] if is_manifest and fields else '')
        candidate = _producer_makefile_path(token, bool(source_dir))
        if candidate in available:
            found.add(candidate)
    return found


def _producer_makefile_path(token: str, from_source_dir: bool) -> str | None:
    """Reject absolute/parent paths before deriving a package-relative path."""
    token = token.removeprefix('./')
    parts = PurePosixPath(token).parts
    if not parts or token.startswith('/') or '..' in parts:
        return None
    directory = token.rstrip('/') if from_source_dir else parts[0]
    return 'input/' + directory + '/Makefile'


def _packaging_excerpt(relative: str, text: str) -> tuple[str, bool]:
    """Render non-comment manifest/config lines with their original positions."""
    excerpt, size, incomplete = [], 0, False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        rendered = f'{relative}:{number}: {line[:500]}'
        if len(line) > 500:
            rendered += ' [line truncated]'
            incomplete = True
        if size + len(rendered) + 1 > 1800:
            return '\n'.join(excerpt), True
        excerpt.append(rendered)
        size += len(rendered) + 1
    return '\n'.join(excerpt), incomplete


def _makefile_excerpt(relative: str, lines: list[str]):
    """Yield variable definitions and recipe starts, preserving source order."""
    positions = set()
    for number, line in enumerate(lines):
        if line.startswith((' ', '\t', '#')):
            continue
        if re.match(r'[\w.-]+\s*[:?+]?=', line):
            positions.add(number)
        elif re.match(r'[\w%./${}()-]+\s*:', line):
            positions.update(range(number, min(number + 4, len(lines))))
    for number in sorted(positions):
        yield f'{relative}:{number + 1}: {lines[number][:500]}'


def enrich_diagnostics(items: list[Diagnostic], worktree: Path) -> None:
    """Apply collectors in the established order; each owns its enable switch."""
    for collector in (_cmake_import_context, _packaging_source_context):
        collector(items, worktree)
