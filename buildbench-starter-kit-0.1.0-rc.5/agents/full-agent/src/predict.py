"""Evaluate build-controlling conditionals under a target architecture.

The hosted run applies the agent's edits untested and builds once at the end,
so the loop has no build to learn from. For the constructs that decide a
cross-architecture build, exact evaluation is cheap without a compiler:

  - C/C++ preprocessor conditionals (#if/#ifdef/#elif/#else) with the target's
    predefined macros and the project's own #defines: is a line reached?
  - GNU make conditionals in debian/rules and Makefiles with DEB_HOST_ARCH and
    friends set for the target: which make-level lines are active, and which
    variables are exported with what value?

Both are tri-state: True, False, or None when an expression uses something
this evaluator does not model (a $(shell ...), a macro from a system header).
A predictor must say "unknown" then, never guess. Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .arch_model import ALL_ARCH_MACROS, LINUX_GCC_DEFINED, NOT_LINUX_GCC, Architecture

Tri = bool | None


# --- C preprocessor -----------------------------------------------------------

_DIRECTIVE = re.compile(r"^\s*#\s*(?P<name>ifdef|ifndef|if|elif|else|endif|define|undef)\b(?P<rest>.*)$")
_DEFINE = re.compile(r"^\s*(?P<name>[A-Za-z_]\w*)(?P<args>\([^)]*\))?\s*(?P<value>.*)$")
_TOKEN = re.compile(r"\s*(?:(?P<num>0[xX][0-9a-fA-F]+|\d+)[uUlL]*|(?P<id>[A-Za-z_]\w*)|(?P<op>&&|\|\||==|!=|<=|>=|[!<>()+\-*/%,?:]))")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, joined text) per logical line, honouring backslash continuations."""
    out, buffer, start = [], [], None
    for number, raw in enumerate(text.split("\n"), start=1):
        if start is None:
            start = number
        if raw.rstrip().endswith("\\"):
            buffer.append(raw.rstrip()[:-1])
            continue
        buffer.append(raw)
        out.append((start, "".join(buffer)))
        buffer, start = [], None
    if buffer:
        out.append((start, "".join(buffer)))
    return out


_COMMENT = re.compile(r"/\*.*?\*/|//.*$")


def _uncomment(expression: str) -> str:
    return _COMMENT.sub(" ", expression).strip()


class _Expr:
    """Recursive-descent evaluation of a #if expression to a tri-state int.

    Identifiers: a known macro has its value; an undefined architecture macro
    is 0 (that is the point); anything else is unknown (None). None
    propagates, except through && and || where one side can decide.
    """

    def __init__(self, text: str, defines: dict[str, int | None], undefined: frozenset[str]):
        self.tokens = []
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if not match or match.end() == position:
                if text[position:].strip():
                    self.tokens = None  # unparseable: unknown
                break
            position = match.end()
            if match.group("num") is not None:
                self.tokens.append(("num", int(match.group("num"), 0)))
            elif match.group("id") is not None:
                self.tokens.append(("id", match.group("id")))
            else:
                self.tokens.append(("op", match.group("op")))
        self.defines, self.undefined, self.i = defines, undefined, 0

    def evaluate(self) -> int | None:
        if self.tokens is None:
            return None
        try:
            value = self._ternary()
            return value if self.i == len(self.tokens) else None
        except (IndexError, ZeroDivisionError, TypeError):
            return None

    def _peek(self, *ops):
        return self.i < len(self.tokens) and self.tokens[self.i] == ("op", ops[0]) if len(ops) == 1 \
            else self.i < len(self.tokens) and self.tokens[self.i][0] == "op" and self.tokens[self.i][1] in ops

    def _take(self):
        token = self.tokens[self.i]
        self.i += 1
        return token

    def _ternary(self):
        condition = self._or()
        if self._peek("?"):
            self._take()
            yes = self._ternary()
            if not self._peek(":"):
                raise IndexError
            self._take()
            no = self._ternary()
            if condition is None:
                return yes if yes == no else None
            return yes if condition else no
        return condition

    def _or(self):
        left = self._and()
        while self._peek("||"):
            self._take()
            right = self._and()
            left = (1 if (left or right) else (None if left is None or right is None else 0))
        return left

    def _and(self):
        left = self._cmp()
        while self._peek("&&"):
            self._take()
            right = self._cmp()
            if left == 0 or right == 0:
                left = 0
            elif left is None or right is None:
                left = None
            else:
                left = 1
        return left

    def _cmp(self):
        left = self._sum()
        while self._peek("==", "!=", "<", ">", "<=", ">="):
            op = self._take()[1]
            right = self._sum()
            if left is None or right is None:
                left = None
            else:
                left = int({"==": left == right, "!=": left != right, "<": left < right,
                            ">": left > right, "<=": left <= right, ">=": left >= right}[op])
        return left

    def _sum(self):
        left = self._term()
        while self._peek("+", "-"):
            op = self._take()[1]
            right = self._term()
            left = None if left is None or right is None else (left + right if op == "+" else left - right)
        return left

    def _term(self):
        left = self._unary()
        while self._peek("*", "/", "%"):
            op = self._take()[1]
            right = self._unary()
            if left is None or right is None:
                left = None
            else:
                left = left * right if op == "*" else (left // right if op == "/" else left % right)
        return left

    def _unary(self):
        if self._peek("!"):
            self._take()
            value = self._unary()
            return None if value is None else int(not value)
        if self._peek("-"):
            self._take()
            value = self._unary()
            return None if value is None else -value
        if self._peek("("):
            self._take()
            value = self._ternary()
            if not self._peek(")"):
                raise IndexError
            self._take()
            return value
        kind, value = self._take()
        if kind == "num":
            return value
        if kind == "id" and value == "defined":
            parenthesised = self._peek("(")
            if parenthesised:
                self._take()
            name = self._take()[1]
            if parenthesised:
                if not self._peek(")"):
                    raise IndexError
                self._take()
            return self._defined(name)
        if kind == "id":
            if value in self.defines:
                return self.defines[value]
            if value in self.undefined:
                return 0
            return None
        raise IndexError

    def _defined(self, name: str) -> int | None:
        if name in self.defines:
            return 1
        if name in self.undefined:
            return 0
        return None


@dataclass
class CppState:
    active: list[Tri]              # per physical line: reached on the target?
    defines: dict[str, int | None]  # macros defined in the file (value or None)


def cpp_active_lines(text: str, target: Architecture,
                     defines: dict[str, int | None] | None = None) -> CppState:
    """Which lines the preprocessor keeps when compiling for ``target``.

    ``defines`` are the project's macros (from config headers, see
    project_defines); the target's architecture macros are defined as 1 and
    every other architecture's are undefined. #define/#undef inside the file
    are followed in order, so a config header evaluates its own guards.
    """
    known: dict[str, int | None] = dict(LINUX_GCC_DEFINED)
    known.update({m: 1 for m in target.macros})
    known.update(dict(target.macro_values))
    known.update(defines or {})
    undefined = frozenset((ALL_ARCH_MACROS - target.macros) | NOT_LINUX_GCC)
    lines = text.split("\n")
    active: list[Tri] = [True] * len(lines)
    # stack of [parent_active, branch_taken_now, some_branch_taken_before]
    stack: list[list[Tri]] = []

    def current() -> Tri:
        if not stack:
            return True
        parent, taken, _ = stack[-1]
        if parent is False or taken is False:
            return False
        return None if parent is None or taken is None else True

    def evaluate(expression: str) -> Tri:
        value = _Expr(_uncomment(expression), known, undefined).evaluate()
        return None if value is None else bool(value)

    for first, logical in _logical_lines(text):
        width = logical.count("\n") + 1  # continuation lines were joined without \n
        state = current()
        match = _DIRECTIVE.match(logical)
        if match:
            name, rest = match.group("name"), match.group("rest").strip()
            if name == "if":
                cond = evaluate(rest) if state is not False else False
                stack.append([state, cond, cond])
            elif name in ("ifdef", "ifndef"):
                rest = _uncomment(rest)
                macro = rest.split()[0] if rest.split() else ""
                defined = (1 if macro in known else 0 if macro in undefined else None)
                cond = None if defined is None else bool(defined) if name == "ifdef" else not defined
                if state is False:
                    cond = False
                stack.append([state, cond, cond])
            elif name == "elif" and stack:
                parent, _, before = stack[-1]
                if before is True or parent is False:
                    cond = False
                else:
                    cond = evaluate(rest)
                    if before is None and cond is not False:
                        cond = None  # an earlier branch may already have been taken
                stack[-1] = [parent, cond, before if before is True else (cond if before is False else None)]
            elif name == "else" and stack:
                parent, _, before = stack[-1]
                cond = False if before is True else (True if before is False else None)
                if parent is False:
                    cond = False
                stack[-1] = [parent, cond, True]
            elif name == "endif" and stack:
                stack.pop()
            elif name == "define" and state is not False:
                define = _DEFINE.match(rest)
                if define and not define.group("args"):
                    value = define.group("value").strip()
                    try:
                        known[define.group("name")] = int(value, 0) if value else 1
                    except ValueError:
                        known[define.group("name")] = None
                elif define:
                    known[define.group("name")] = None
            elif name == "undef" and state is not False:
                known.pop(rest.split()[0] if rest.split() else "", None)
            # the directive line itself is "reached" when its parent is
            for k in range(first - 1, min(first - 1 + max(width, 1), len(lines))):
                active[k] = state
            continue
        for k in range(first - 1, min(first - 1 + max(width, 1), len(lines))):
            active[k] = state
    return CppState(active, {k: v for k, v in known.items()
                             if k not in target.macros and k not in LINUX_GCC_DEFINED})


def cpp_reached(text: str, line: int, target: Architecture,
                defines: dict[str, int | None] | None = None) -> Tri:
    """Is physical line ``line`` (1-based) compiled for ``target``?"""
    state = cpp_active_lines(text, target, defines)
    if not 1 <= line <= len(state.active):
        return None
    return state.active[line - 1]


_PROJECT_DEFINE = re.compile(r"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)\s*(?P<value>[^/]*)")
MAX_HEADER_FILES = 600
MAX_HEADER_BYTES = 256 * 1024


def project_defines(worktree: Path, names: set[str], target: Architecture) -> dict[str, int | None]:
    """Values of the project's own macros ``names`` (HAVE_*, USE_*, ...), read
    from headers under input/ and evaluated under ``target`` so that a
    conditional #define (inside #if __x86_64__) is honoured. A name defined in
    two files with different values is unknown."""
    return {name: value for name, (value, _) in define_locations(worktree, names, target).items()}


def define_locations(worktree: Path, names: set[str], target: Architecture) -> dict[str, tuple[int | None, str]]:
    """Each macro's (value, "path:line: text") on the target, as project_defines."""
    from .tools import collect_files
    root = Path(worktree) / "input"
    if not names or not root.is_dir():
        return {}
    try:
        files, _ = collect_files(root)
    except OSError:
        return {}
    found: dict[str, tuple[int | None, str]] = {}
    conflicting: set[str] = set()
    scanned = 0
    for path in files:
        if path.suffix not in (".h", ".hpp", ".hh", ".hxx", ".in", ".cmake"):
            continue
        if "/.pc/" in path.as_posix():
            continue
        try:
            if path.stat().st_size > MAX_HEADER_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not any(name in text for name in names):
            continue
        scanned += 1
        if scanned > MAX_HEADER_FILES:
            break
        state = cpp_active_lines(text, target)
        for number, line in enumerate(text.split("\n")):
            match = _PROJECT_DEFINE.match(line)
            if not match or match.group("name") not in names or state.active[number] is False:
                continue
            raw = match.group("value").strip()
            try:
                value: int | None = int(raw, 0) if raw else 1
            except ValueError:
                value = None
            name = match.group("name")
            where = f"{path.relative_to(worktree).as_posix()}:{number + 1}: {line.strip()[:120]}"
            if name in found and found[name][0] != value:
                conflicting.add(name)
            found[name] = (value, where)
    for name in conflicting:
        found[name] = (None, found[name][1] + " (and elsewhere with another value)")
    return found


# --- GNU make -------------------------------------------------------------------

_MAKE_COND = re.compile(r"^(?P<name>ifeq|ifneq|ifdef|ifndef|else|endif)\b(?P<rest>.*)$")
_MAKE_ASSIGN = re.compile(r"^(?P<export>export\s+)?(?P<name>[A-Za-z_][\w.-]*)\s*(?P<op>::=|:=|\+=|\?=|=)(?P<value>.*)$")
_MAKE_EXPORT = re.compile(r"^export\s+(?P<names>[A-Za-z_][\w.\s-]*)$")
_VAR_REF = re.compile(r"\$[({](?P<name>[A-Za-z_][\w.-]*)[)}]|\$(?P<short>[A-Za-z_])")


def make_variables(target: Architecture) -> dict[str, str]:
    """dpkg-architecture's variables for a native build on ``target``."""
    return {
        "DEB_HOST_ARCH": target.dpkg, "DEB_BUILD_ARCH": target.dpkg, "DEB_TARGET_ARCH": target.dpkg,
        "DEB_HOST_MULTIARCH": target.gnu, "DEB_BUILD_MULTIARCH": target.gnu,
        "DEB_HOST_GNU_TYPE": target.gnu, "DEB_BUILD_GNU_TYPE": target.gnu,
        "DEB_HOST_ARCH_CPU": target.uname if target.dpkg != "i386" else "i386",
        "DEB_HOST_ARCH_OS": "linux", "DEB_HOST_ARCH_BITS": str(target.pointer_bits),
        "DEB_HOST_ARCH_ENDIAN": "little" if target.little_endian else "big",
    }


@dataclass
class MakeState:
    active: list[Tri]                        # per line: at make level and active?
    variables: dict[str, str | None]         # final values (None = unknown)
    exported: dict[str, str | None]          # exported variables and their values
    flags: dict[str, str | None]             # DEB_*_MAINT_* values that reach dpkg-buildflags


def _expand(value: str, variables: dict[str, str | None]) -> str | None:
    """Expand $(VAR) references; None when something is not modelled."""
    if "$(shell" in value or "${shell" in value or "$(eval" in value:
        return None
    unknown = False

    def one(match):
        nonlocal unknown
        name = match.group("name") or match.group("short")
        if name == "@" or name == "<" or name == "^":
            return ""
        if name in variables:
            inner = variables[name]
            if inner is None:
                unknown = True
            return inner or ""
        unknown = unknown or name.isupper() and name not in ("CURDIR",)  # a Debian variable we lack
        return ""

    # function calls other than filter/findstring/strip are not modelled
    if re.search(r"\$\((?:filter|findstring|strip|if|or|and|wildcard|patsubst|subst|word)\b", value):
        value = _functions(value, variables)
        if value is None:
            return None
    expanded = _VAR_REF.sub(one, value)
    return None if unknown else expanded.strip()


def _functions(value: str, variables: dict[str, str | None]) -> str | None:
    """$(filter pattern..., text) and $(findstring find, in) with expanded arguments."""
    def repl(match):
        name, args = match.group(1), match.group(2)
        parts = [p.strip() for p in args.split(",", 1)]
        if len(parts) != 2:
            return "\0"
        a, b = (_expand(p, variables) for p in parts)
        if a is None or b is None:
            return "\0"
        if name == "filter":
            return " ".join(w for w in b.split() if any(_glob(w, pat) for pat in a.split()))
        if name == "findstring":
            return a if a and a in b else ""
        return "\0"
    while True:
        new = re.sub(r"\$\((filter|findstring)\s+((?:[^$()]|\$\([^()]*\))*)\)", repl, value)
        if "\0" in new:
            return None
        if new == value:
            break
        value = new
    if re.search(r"\$\((?:strip|if|or|and|wildcard|patsubst|subst|word)\b", value):
        return None
    return value


def _glob(word: str, pattern: str) -> bool:
    if "%" not in pattern:
        return word == pattern
    head, tail = pattern.split("%", 1)
    return word.startswith(head) and word.endswith(tail) and len(word) >= len(head) + len(tail)


def make_active_lines(text: str, target: Architecture) -> MakeState:
    """Evaluate a Makefile's make-level conditionals and assignments for
    ``target``. Recipe lines (tab-indented) are reported with the state of the
    rule they belong to but their contents are not interpreted."""
    variables: dict[str, str | None] = dict(make_variables(target))
    exported: dict[str, str | None] = {}
    lines = text.split("\n")
    active: list[Tri] = [True] * len(lines)
    stack: list[list[Tri]] = []

    def current() -> Tri:
        if not stack:
            return True
        parent, taken, _ = stack[-1]
        if parent is False or taken is False:
            return False
        return None if parent is None or taken is None else True

    def condition(name: str, rest: str) -> Tri:
        rest = rest.strip()
        if name in ("ifdef", "ifndef"):
            var = rest.split()[0] if rest.split() else ""
            value = variables.get(var)
            if var not in variables:
                defined: Tri = False if var.startswith("DEB_") or var.isupper() is False else None
                defined = False if var not in variables and not var.startswith("DEB_") else defined
            else:
                defined = None if value is None else bool(value)
            if defined is None:
                return None
            return defined if name == "ifdef" else not defined
        # ifeq (a,b) | ifeq "a" "b" | ifeq 'a' 'b'
        match = re.match(r"^\((?P<a>.*?),(?P<b>.*)\)$", rest) or \
            re.match(r"^[\"'](?P<a>[^\"']*)[\"']\s+[\"'](?P<b>[^\"']*)[\"']$", rest)
        if not match:
            return None
        a, b = _expand(match.group("a").strip(), variables), _expand(match.group("b").strip(), variables)
        if a is None or b is None:
            return None
        equal = a.strip() == b.strip()
        return equal if name == "ifeq" else not equal

    for first, logical in _logical_lines(text):
        width = max(1, logical.count("\n") + 1)
        state = current()
        stripped = logical.strip()
        cond = _MAKE_COND.match(stripped) if not logical.startswith("\t") else None
        if cond:
            name, rest = cond.group("name"), cond.group("rest")
            if name in ("ifeq", "ifneq", "ifdef", "ifndef"):
                value = condition(name, rest) if state is not False else False
                stack.append([state, value, value])
            elif name == "else" and stack:
                parent, _, before = stack[-1]
                # "else ifeq (...)" is a new condition on the else branch
                nested = _MAKE_COND.match(rest.strip())
                if before is True or parent is False:
                    value: Tri = False
                elif nested and nested.group("name") in ("ifeq", "ifneq", "ifdef", "ifndef"):
                    value = condition(nested.group("name"), nested.group("rest"))
                    if before is None and value is not False:
                        value = None
                else:
                    value = True if before is False else None
                stack[-1] = [parent, value, True if (before is True or value is True) else (None if before is None or value is None else False)]
            elif name == "endif" and stack:
                stack.pop()
        elif not logical.startswith("\t") and state is not False and not stripped.startswith("#"):
            export_only = _MAKE_EXPORT.match(stripped)
            assign = _MAKE_ASSIGN.match(stripped)
            if assign and not stripped.startswith("export") or (assign and assign.group("op")):
                name, op, raw = assign.group("name"), assign.group("op"), assign.group("value").strip()
                value = _expand(raw, variables) if op in ("=", ":=", "::=", "?=", "+=") else None
                if state is None:
                    new_value: str | None = None  # may or may not be assigned
                elif op == "?=":
                    new_value = variables.get(name) if name in variables else value
                elif op == "+=":
                    previous = variables.get(name, "")
                    new_value = None if previous is None or value is None else (previous + " " + value).strip()
                else:
                    new_value = value
                variables[name] = new_value
                if assign.group("export"):
                    exported[name] = new_value
            elif export_only:
                for name in export_only.group("names").split():
                    exported[name] = variables.get(name, "")
        for k in range(first - 1, min(first - 1 + width, len(lines))):
            active[k] = state
    for name in exported:
        exported[name] = variables.get(name, exported[name])
    flags = {name: value for name, value in exported.items()
             if re.match(r"DEB_(?:[A-Z]+FLAGS_MAINT_(?:APPEND|PREPEND|STRIP|SET)|BUILD_MAINT_OPTIONS)$", name)}
    return MakeState(active, variables, exported, flags)
