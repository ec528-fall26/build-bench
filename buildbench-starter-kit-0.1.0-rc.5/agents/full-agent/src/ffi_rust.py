"""Small Rust source reader for FFI evidence, deliberately not a Rust compiler.

Unsupported constructs are left unresolved. Explicit use/type aliases are
resolved against indexed source, never against package names or build output.
"""
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re

MAX_TOKENS = 40000
IDENT = re.compile(r'^[A-Za-z_]\w*$')
TOKEN = re.compile(r'//[^\n]*|/\*|(?:br|r)\#*"|(?:b|c)?"(?:\\.|[^"\\])*"'
                   r"|'(?:\\(?:u\{[^}]*\}|x[0-9a-fA-F]{2}|.)|[^'\\])'"
                   r'|\b[A-Za-z_]\w*\b|::|->|==|!=|=>|\S')
CHAR = {'std::ffi::c_char', 'core::ffi::c_char', 'std::os::raw::c_char'}
CSTR = {'std::ffi::CStr::from_ptr', 'core::ffi::CStr::from_ptr'}
CSTR_KEY = ('<Rust standard library>', 'CStr::from_ptr')
CSTR_DOC = 'https://doc.rust-lang.org/std/ffi/struct.CStr.html#method.from_ptr'
PASSIVE_ATTRIBUTES = {'doc', 'allow', 'warn', 'deny', 'forbid', 'inline', 'cold',
                      'must_use', 'deprecated'}


def tokens(text):
    result, pos = [], 0
    while match := TOKEN.search(text, pos):
        word, pos = match.group(), match.end()
        if word.startswith('//'):
            continue
        if word == '/*':
            depth = 1
            while depth and pos < len(text):
                if text.startswith('/*', pos):
                    depth, pos = depth + 1, pos + 2
                elif text.startswith('*/', pos):
                    depth, pos = depth - 1, pos + 2
                else:
                    pos += 1
            if depth:
                raise ValueError('unterminated comment')
            continue
        if re.fullmatch(r'(?:br|r)\#*"', word):
            ending = '"' + '#' * word.count('#')
            end = text.find(ending, pos)
            if end < 0:
                raise ValueError('unterminated raw string')
            pos, word = end + len(ending), '"<raw string>"'
        result.append((word, match.start()))
        if len(result) > MAX_TOKENS:
            raise ValueError('token limit')
    return result


def closing(words, start):
    pairs, stack = {'(': ')', '{': '}', '[': ']'}, []
    for i in range(start, len(words)):
        word = words[i]
        if word in pairs:
            stack.append(pairs[word])
        elif word in pairs.values():
            if not stack or stack.pop() != word:
                raise ValueError('unbalanced delimiters')
            if not stack:
                return i
    raise ValueError('unclosed delimiter')


def split_args(words):
    parts, start, depth = [], 0, 0
    for i, word in enumerate(words):
        if word in {'(', '[', '{', '<'}:
            depth += 1
        elif word in {')', ']', '}', '>'}:
            depth -= 1
        elif word == ',' and depth == 0:
            parts.append(words[start:i])
            start = i + 1
    if words[start:]:
        parts.append(words[start:])
    return parts


@dataclass
class Function:
    path: str
    name: str
    line: int
    foreign: bool
    params: list
    body: list
    raw_params: list = field(default_factory=list)
    raw_return: list = field(default_factory=list)
    return_type: tuple | None = None

    @property
    def key(self):
        return self.path, self.name


@dataclass
class Unit:
    functions: list = field(default_factory=list)
    imports: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    blocked: set = field(default_factory=set)
    unknown: list = field(default_factory=list)
    declaration_lines: dict = field(default_factory=dict)


def add_unique(mapping, name, value):
    mapping[name] = value if name not in mapping else None


def use_items(words, prefix=(), depth=0):
    if depth > 16:
        raise ValueError('import nesting limit')
    if '{' in words:
        start = words.index('{')
        if closing(words, start) != len(words) - 1:
            raise ValueError('unsupported use group')
        parent = tuple(w for w in words[:start] if w != '::')
        for item in split_args(words[start + 1:-1]):
            yield from use_items(item, prefix + parent, depth + 1)
        return
    if '*' in words:
        raise ValueError('wildcard import unresolved')
    rename = None
    if 'as' in words:
        pos = words.index('as')
        if len(words[pos + 1:]) != 1:
            raise ValueError('unsupported import alias')
        rename, words = words[pos + 1], words[:pos]
    path = prefix + tuple(w for w in words if w != '::')
    if path and path[-1] == 'self':
        path = path[:-1]
    if not path or not all(IDENT.fullmatch(part) for part in path):
        raise ValueError('unsupported use path')
    yield rename or path[-1], path


def parse_file(path, text):
    ts, unit = tokens(text), Unit()
    words = [token[0] for token in ts]

    def scan(start, end, foreign=False):
        i = start
        while i < end:
            word = words[i]
            if word == '#':
                attr = i + 2 if words[i + 1:i + 2] == ['!'] else i + 1
                if attr >= end or words[attr] != '[':
                    raise ValueError('unsupported attribute')
                stop = closing(words, attr)
                if words[attr + 1] not in PASSIVE_ATTRIBUTES:
                    # Do not guess cfg or proc-macro expansion for this file.
                    raise ValueError('attributes/cfg need resolution')
                i = stop + 1
                continue
            if word in {'use', 'type'}:
                stop = i + 1
                while stop < end and words[stop] != ';':
                    if words[stop] in {'{', '(', '['}:
                        stop = closing(words, stop)
                    stop += 1
                if stop == end:
                    raise ValueError('unterminated declaration')
                if word == 'use':
                    try:
                        for name, target in use_items(words[i + 1:stop]):
                            add_unique(unit.imports, name, target)
                            unit.declaration_lines[name] = text.count('\n', 0, ts[i][1]) + 1
                    except ValueError as error:
                        unit.unknown.append(f'{path}: {error}')
                elif words[i + 2:i + 3] == ['=']:
                    add_unique(unit.aliases, words[i + 1], words[i + 3:stop])
                    unit.declaration_lines[words[i + 1]] = text.count('\n', 0, ts[i][1]) + 1
                else:
                    unit.blocked.add(words[i + 1])
                    unit.unknown.append(f'{path}: generic type alias unresolved')
                i = stop + 1
                continue
            if word in {'struct', 'enum', 'mod', 'trait', 'union'} and i + 1 < end:
                unit.blocked.add(words[i + 1])
            if words[i:i + 3] == ['extern', '"C"', '{']:
                stop = closing(words, i + 2)
                scan(i + 3, stop, True)
                i = stop + 1
                continue
            if word == 'fn' and i + 2 < end and words[i + 2] == '(':
                stop = closing(words, i + 2)
                params, raw_params = [], []
                for part in split_args(words[i + 3:stop]):
                    if part[:1] == ['mut']:
                        part = part[1:]
                    if len(part) < 3 or part[1] != ':' or not IDENT.fullmatch(part[0]):
                        params.append(('', None))
                        raw_params.append([])
                    else:
                        params.append((part[0], None))
                        raw_params.append(part[2:])
                j = stop + 1
                while j < end and words[j] not in {'{', ';'}:
                    j += 1
                if j == end:
                    raise ValueError('missing function body')
                return_words = words[stop + 2:j] if words[stop + 1:stop + 2] == ['->'] else []
                body, end_function = [], j
                if words[j] == '{':
                    end_function = closing(words, j)
                    body = words[j + 1:end_function]
                elif not foreign:
                    unit.unknown.append(f'{path}: non-foreign declaration {words[i + 1]}')
                    i = j + 1
                    continue
                if 'where' in return_words:
                    unit.unknown.append(f'{path}: generic constraints unresolved')
                    body, return_words = [], []
                line = text.count('\n', 0, ts[i][1]) + 1
                unit.functions.append(Function(path, words[i + 1], line, foreign,
                                                params, body, raw_params, return_words))
                i = end_function + 1
                continue
            if word == 'fn':
                unit.unknown.append(f'{path}: generic function unresolved')
            if word == 'include' and words[i + 1:i + 2] == ['!']:
                unit.unknown.append(f'{path}: include! requires generated/source declarations; '
                                    'unavailable bindings are not inferred')
            if word in {'{', '('}:
                if word == '{':
                    unit.unknown.append(f'{path}: inline module, impl, macro or unsupported block')
                i = closing(words, i) + 1
            else:
                i += 1
    scan(0, len(words))
    return unit


class Resolver:
    def __init__(self, units):
        self.units = units
        self.evidence = set()

    def symbol(self, path, names, seen=()):
        """Return (file, name) or ('<builtin>', fully qualified standard name)."""
        names = tuple(names)
        marker = path, names
        if not names or marker in seen or len(seen) >= 16:
            return None
        unit = self.units.get(path)
        if unit is None:
            return None
        first = names[0]
        if first in unit.imports:
            target = unit.imports[first]
            if (target is None or first in unit.aliases or first in unit.blocked or
                    any(function.name == first for function in unit.functions)):
                return None
            self.evidence.add(f'{path}:{unit.declaration_lines[first]} use {"::".join(target)} as {first}')
            return self.symbol(path, target + names[1:], seen + (marker,))
        if first in {'std', 'core'} and first not in unit.blocked and first not in unit.aliases:
            full = '::'.join(names)
            return ('<builtin>', full) if full in CHAR | CSTR else None
        if len(names) == 1:
            if first in unit.blocked:
                return None
            return path, first
        if '/src/' not in path or first not in {'crate', 'self', 'super'}:
            return None
        root, relative = path.rsplit('/src/', 1)
        pieces = list(PurePosixPath(relative).parts)
        module = pieces[:-1] + ([] if pieces[-1] in {'lib.rs', 'main.rs', 'mod.rs'}
                               else [PurePosixPath(relative).stem])
        rest = list(names)
        if first == 'crate':
            module, rest = [], rest[1:]
        elif first == 'self':
            rest = rest[1:]
        else:
            while rest and rest[0] == 'super':
                if not module:
                    return None
                module.pop()
                rest.pop(0)
        if not rest:
            return None
        module += rest[:-1]
        base = root + '/src/'
        candidates = ([base + '/'.join(module) + '.rs', base + '/'.join(module) + '/mod.rs']
                      if module else [base + 'lib.rs', base + 'main.rs'])
        existing = [candidate for candidate in candidates if candidate in self.units]
        if len(existing) != 1:
            return None
        target = existing[0]
        if target == path and rest[-1] not in unit.imports:
            return (target, rest[-1]) if rest[-1] not in unit.blocked else None
        return self.symbol(target, (rest[-1],), seen + (marker,))

    def expand_type(self, path, words, seen=()):
        marker = path, tuple(words)
        if marker in seen or len(seen) >= 16:
            return None
        if words[:2] in (['*', 'const'], ['*', 'mut']):
            inner = self.expand_type(path, words[2:], seen + (marker,))
            return words[:2] + inner if inner else None
        if len(words) == 1 and words[0] in {'i8', 'u8'}:
            return words
        if not words or any(not IDENT.fullmatch(word) for word in words[::2]) or any(
                word != '::' for word in words[1::2]) or len(words) % 2 == 0:
            return None
        target = self.symbol(path, words[::2])
        if target is None:
            return None
        if target[0] == '<builtin>':
            return ['@c_char'] if target[1] in CHAR else None
        alias = self.units[target[0]].aliases.get(target[1])
        if alias:
            unit = self.units[target[0]]
            self.evidence.add(f'{target[0]}:{unit.declaration_lines[target[1]]} '
                              f'type {target[1]} = {" ".join(alias)}')
        return self.expand_type(target[0], alias, seen + (marker,)) if alias else None

    def pointer(self, path, words):
        expanded = self.expand_type(path, words)
        if expanded and len(expanded) == 3 and expanded[:2] in (['*', 'const'], ['*', 'mut']):
            return expanded[1], 'c_char' if expanded[2] == '@c_char' else expanded[2]
        return None
