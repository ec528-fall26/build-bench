"""Resolve supported direct calls, immutable local aliases and pointer returns."""
from .ffi_rust import CSTR, CSTR_KEY, Function, IDENT, closing, split_args

MAX_EDGES = 2000
MAX_BODY_TOKENS = 8000
MAX_BINDINGS = 256


def slot_type(index, key):
    group = index.functions.get(key[:2], [])
    if len(group) != 1:
        return None
    if key[2] == -1:
        return group[0].return_type
    if key[2] < 0 or key[2] >= len(group[0].params):
        return None
    return group[0].params[key[2]][1]


def statements(words):
    """Top-level semicolon-delimited statements, including an optional tail."""
    start = i = 0
    while i < len(words):
        if words[i] in {'(', '[', '{'}:
            i = closing(words, i) + 1
            continue
        if words[i] == ';':
            yield start, i, True
            start = i + 1
        i += 1
    if start < len(words):
        yield start, len(words), False


def link_functions(index, resolver):
    for group in list(index.functions.values()):
        if len(group) != 1:
            index.unknown.append(f'ambiguous function: {group[0].key}')
            continue
        function = group[0]
        if not function.body:
            continue
        linker = FlowReader(index, resolver, function)
        linker.run()
        if len(index.edges) >= MAX_EDGES:
            index.unknown.append('flow edge limit reached')
            return


class FlowReader:
    def __init__(self, index, resolver, function):
        self.index, self.resolver, self.function = index, resolver, function
        self.words = function.body
        # A single unsafe block adds no local bindings or branching of its own.
        if (self.words[:2] == ['unsafe', '{'] and
                closing(self.words, 1) == len(self.words) - 1):
            self.words = self.words[2:-1]
        self.bindings = []
        self.assignments = []

    def note(self, message):
        self.index.unknown.append(f'{self.function.path}:{self.function.line}: '
                                  f'{self.function.name}: {message}')

    def lookup(self, name, pos):
        names = name[::2]
        if not names or len(name) % 2 == 0 or any(word != '::' for word in name[1::2]):
            return None
        if any(not IDENT.fullmatch(word) for word in names):
            return None
        if names[0] in {p[0] for p in self.function.params} or any(
                names[0] in binding['names'] and binding['end'] < pos for binding in self.bindings):
            return None  # local/function-pointer target, not a resolved function
        target = self.resolver.symbol(self.function.path, names)
        if target and target[0] == '<builtin>' and target[1] in CSTR:
            self.index.functions.setdefault(CSTR_KEY, [Function(
                CSTR_KEY[0], CSTR_KEY[1], 0, True, [('ptr', ('const', 'c_char'))], [])])
            return CSTR_KEY
        return target if target and len(self.index.functions.get(target, [])) == 1 else None

    def expression(self, words, pos, seen=()):
        if len(seen) >= 16:
            self.note('local alias chain limit reached')
            return None
        while words[:1] == ['('] and closing(words, 0) == len(words) - 1:
            words, pos = words[1:-1], pos + 1
        if words[:2] == ['unsafe', '{'] and closing(words, 1) == len(words) - 1:
            return self.expression(words[2:-1], pos + 2, seen)
        if len(words) == 1 and IDENT.fullmatch(words[0]):
            name = words[0]
            if any(var == name and at < pos for var, at in self.assignments):
                return None
            bindings = [item for item in self.bindings if name in item['names'] and item['end'] < pos]
            if bindings:
                binding = bindings[-1]
                if not binding['simple'] or binding['start'] in seen:
                    return None
                return self.expression(binding['rhs'], binding['rhs_start'], seen + (binding['start'],))
            for ordinal, (param_name, typ) in enumerate(self.function.params):
                if name == param_name and typ:
                    return self.function.key + (ordinal,)
            return None
        if '(' in words:
            opening = words.index('(')
            if closing(words, opening) == len(words) - 1:
                target = self.lookup(words[:opening], pos)
                if target and slot_type(self.index, target + (-1,)):
                    return target + (-1,)
        return None

    def read_bindings(self):
        """Local declarations shadow only after their initializer finishes.

        Aliases are followed only for simple immutable top-level lets. Other
        bindings still hide the old name, so they cannot become false edges.
        """
        words = self.words
        top_starts = {start for start, _, _ in statements(words)}
        lhs_equals = set()
        for i, word in enumerate(words):
            if word != 'let':
                continue
            end = i + 1
            while end < len(words) and words[end] != ';':
                if words[end] in {'(', '[', '{'}:
                    end = closing(words, end)
                end += 1
            segment = words[i + 1:end]
            if '=' not in segment:
                self.note('uninitialized local binding unresolved')
                names = {word for word in segment if IDENT.fullmatch(word)}
                rhs, rhs_start, simple = [], end, False
            else:
                eq = i + 1 + segment.index('=')
                lhs_equals.add(eq)
                lhs = words[i + 1:eq]
                # On complex patterns over-approximate names to avoid false resolution.
                names = {word for word in lhs if IDENT.fullmatch(word)}
                rhs, rhs_start = words[eq + 1:end], eq + 1
                simple = len(lhs) == 1 and i in top_starts
            self.bindings.append({'start': i, 'end': end, 'names': names,
                                  'rhs': rhs, 'rhs_start': rhs_start, 'simple': simple})
        for i, word in enumerate(words):
            if word == '=' and i not in lhs_equals and i:
                before = i - 2 if words[i - 1] in {'+', '-', '*', '/', '|', '&', '^', '%'} else i - 1
                if before >= 0 and IDENT.fullmatch(words[before]):
                    self.assignments.append((words[before], i))
            if words[i:i + 2] == ['&', 'mut'] and i + 2 < len(words):
                self.assignments.append((words[i + 2], i))

    def edge(self, source, target):
        if len(self.index.edges) >= MAX_EDGES:
            return
        if slot_type(self.index, source) and slot_type(self.index, target):
            self.index.edges.add((source, target))
        else:
            self.note('flow has an unresolved pointer type')

    def run(self):
        words = self.words
        if len(words) > MAX_BODY_TOKENS or words.count('let') > MAX_BINDINGS:
            self.note('function flow scan limit reached')
            return
        if any(word in words for word in ('fn', '!', 'for', 'match', '|', '#', 'use')):
            self.note('macros, nested functions, closures, imports or pattern scopes unresolved')
            return
        self.read_bindings()
        for i, word in enumerate(words):
            if word != '(' or i == 0 or not IDENT.fullmatch(words[i - 1]):
                continue
            start = i - 1
            while start >= 2 and words[start - 1] == '::' and IDENT.fullmatch(words[start - 2]):
                start -= 2
            if words[start] in {'if', 'while', 'loop', 'return'}:
                continue
            if start and words[start - 1] == '.':
                self.note('method call unresolved; receiver type not inferred')
                continue
            target = self.lookup(words[start:i], start)
            if target is None:
                self.note(f'unresolved call {"".join(words[start:i])}')
                continue
            callee = self.index.functions[target][0]
            args = split_args(words[i + 1:closing(words, i)])
            if len(args) != len(callee.params):
                self.note('argument count unknown')
                continue
            offset = i + 1
            for ordinal, arg in enumerate(args):
                source = self.expression(arg, offset)
                if source:
                    self.edge(source, target + (ordinal,))
                elif callee.params[ordinal][1]:
                    self.note(f'argument {ordinal} to {callee.name} unresolved or transformed '
                              '(casts do not establish a portable source type)')
                offset += len(arg) + 1
        if self.function.return_type:
            if any(word in words for word in ('if', 'else', 'while', 'loop', '?')):
                self.note('branching return flow unresolved')
                return
            for start, end, terminated in statements(words):
                expr = words[start:end]
                is_return = expr[:1] == ['return']
                if is_return or (not terminated and expr[:1] != ['let']):
                    source = self.expression(expr[1:] if is_return else expr,
                                             start + int(is_return))
                    if source:
                        self.edge(self.function.key + (-1,), source)
                        # A return expression and its declared result must agree.
                        # Propagate an expectation imposed by a downstream caller
                        # back into a directly returned parameter as well.
                        if source[:2] == self.function.key:
                            self.edge(source, self.function.key + (-1,))
                    else:
                        self.note('return expression unresolved')
