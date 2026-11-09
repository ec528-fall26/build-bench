"""static-archive-shim: link a non-PIC static archive's objects into a Python extension.

pybigwig (resolute, amd64): the extension links -lBigWig, but libbigwig0t64
ships no libBigWig.so on amd64, so ld falls back to the static archive, whose
objects hold R_X86_64_PC32 relocations against `stderr` that a shared object
cannot contain. The earlier verdict "not repairable in this package" was wrong:
a setup.py edit passed the validator (692 s;
harness/replays/pybigwig-verified-static-shim.json). At build time it unpacks
the archive with `ar x`, adds a shim that defines a hidden `FILE *stderr`
(opened with fdopen(2) in a constructor) so the PC32 references resolve inside
the object, passes the objects as extra_objects with -Wl,-Bsymbolic, and drops
the library from `libraries`. Tier "verified-template": the import was tested
in a container only, and the generated text is the verified one with the
library name filled in.

Standard library only.
"""

from __future__ import annotations

import ast
import re

from .. import patterns
from ..types import Edit
from .registry import Match, Recipe, Refusal, host_arch, read, register

ID = "static-archive-shim"
# standard stream -> (file descriptor, fdopen mode, setvbuf mode or None)
STREAMS = {"stderr": (2, "w", 2), "stdout": (1, "w", None), "stdin": (0, "r", None)}


def _libs_line(setup: str, name: str):
    """(line, items) of the first column-0 `libs = [...]` list that names the library."""
    for found in re.finditer(r"^libs\s*=\s*(\[[^\n\]]*\])[ \t]*$", setup, re.M):
        try:
            items = ast.literal_eval(found.group(1))
        except (ValueError, SyntaxError):
            continue
        if isinstance(items, list) and all(isinstance(item, str) for item in items) and name in items:
            return found.group(0), items
    return None


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    hits = []
    for index, line in enumerate(log_lines):
        found = patterns._NONPIC.search(line) if isinstance(line, str) else None
        if found and found.group("reloc").endswith("PC32"):
            hits.append((index, found))
    if not hits:
        return None
    index, first = hits[0]
    name = first.group("name")
    setup = read(worktree, "input/setup.py")
    if not setup or not _libs_line(setup, name):
        return None
    symbols = list(dict.fromkeys(re.sub(r"@.*$", "", m.group("symbol")) for _, m in hits))
    packages = []
    for line in log_lines:
        got = patterns._GET_PACKAGE.search(line) if isinstance(line, str) else None
        if got and name.lower() in got.group("pkg").lower() and not got.group("pkg").endswith("-dev"):
            packages.append(got.group("pkg"))
    return Match(ID, evidence=[log_lines[index]],
                 params={"name": name, "archive": first.group("archive"), "symbols": symbols,
                         "package": packages[0] if packages else "",
                         "arch": host_arch(log_lines, target_arch)}, line=index)


def shim_source_literal(symbols) -> str:
    """The shim's C text as the Python string concatenation written into setup.py."""
    pieces = ['"struct _IO_FILE; typedef struct _IO_FILE FILE;\\n"',
              '"extern FILE *fdopen(int, const char *);\\n"',
              '"extern int setvbuf(FILE *, char *, int, unsigned long);\\n"']
    body = []
    for symbol in symbols:
        fd, mode, buffering = STREAMS[symbol]
        pieces.append(f'"__attribute__((visibility(\\"hidden\\"))) FILE *{symbol};\\n"')
        body.append(f'{symbol} = fdopen({fd}, \\"{mode}\\");'
                    + (f" if ({symbol}) setvbuf({symbol}, 0, {buffering}, 0);" if buffering is not None else ""))
    pieces.append('"__attribute__((constructor)) static void bb_init(void) { ' + " ".join(body) + ' }\\n"')
    return "\n                ".join(pieces)


def render(name: str, items: list[str], symbols, package: str, arch: str) -> str:
    ident = re.sub(r"\W", "_", name).lower()
    who = f"{package} ships" if package else "the runtime package ships"
    where = f" on {arch}" if arch else ""
    names = " and ".join(symbols) if len(symbols) < 3 else ", ".join(symbols)
    kept = ", ".join(f'"{item}"' for item in items if item != name)
    full = ", ".join(f'"{item}"' for item in items)
    shim_name = "_".join(symbols) if len(symbols) > 1 else symbols[0]
    return f'''import os, shlex, shutil, subprocess

def _static_lib{ident}():
    """{who} no lib{name}.so{where}, so -l{name} falls back to the
    non-PIC lib{name}.a, which ld refuses to put into a shared object
    (R_X86_64_PC32 against {names}).  Unpack the archive and link its objects
    directly; a hidden {names} defined in a tiny shim makes the PC32 references legal."""
    cc = shlex.split(os.environ.get("CC") or "gcc")
    def find(name):
        p = subprocess.run(cc + ["-print-file-name=" + name], capture_output=True, text=True).stdout.strip()
        return p if os.path.isabs(p) and os.path.exists(p) else None
    archive = find("lib{name}.a")
    if find("lib{name}.so") or not archive:
        return [], []
    d = os.path.join("build", "lib{ident}-static")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    subprocess.check_call(["ar", "x", archive], cwd=d)
    shim = os.path.join(d, "{shim_name}_shim.c")
    with open(shim, "w") as f:
        f.write({shim_source_literal(symbols)})
    objs = sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".o"))
    return objs, [shim]

_extra_objs, _extra_srcs = _static_lib{ident}()
{{SOURCES}}.extend(_extra_srcs)
extra_link = ["-Wl,-Bsymbolic"] if _extra_objs else []
libs = [{kept}] if _extra_objs else [{full}]
'''


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/setup.py"
    setup = read(worktree, path)
    if not setup:
        raise Refusal("input/setup.py cannot be read.")
    name, symbols = match.params["name"], match.params["symbols"]
    odd = [s for s in symbols if s not in STREAMS]
    if odd:
        raise Refusal(f"the non-PIC relocations are against {', '.join(odd)}, not only the C standard streams "
                      "the verified shim defines; adapt setup.py yourself.")
    if re.search(r"extra_objects|extra_link_args", setup):
        raise Refusal("setup.py already sets extra_objects or extra_link_args; merge the shim by hand.")
    libs = _libs_line(setup, name)
    if not libs:
        raise Refusal(f"setup.py has no `libs = [...]` list naming {name}.")
    if not re.search(r"libraries\s*=\s*libs\b", setup):
        raise Refusal("the Extension in setup.py does not take `libraries = libs`; adapt it by hand.")
    sources = re.search(r"^(?P<line>(?P<indent>[ \t]+)sources\s*=\s*(?P<var>[A-Za-z_]\w*)\s*,[ \t]*)$", setup, re.M)
    if not sources:
        raise Refusal("the Extension in setup.py has no `sources = <list variable>,` line to extend.")
    line, items = libs
    if setup.count(line + "\n") != 1 or setup.count(sources.group("line") + "\n") != 1:
        raise Refusal("the setup.py lines to change are not unique.")
    new = render(name, items, symbols, match.params.get("package", ""), match.params.get("arch", ""))
    new = new.replace("{SOURCES}", sources.group("var"))
    indent = sources.group("indent")
    return [Edit(path, line + "\n", new),
            Edit(path, sources.group("line") + "\n",
                 sources.group("line") + f"\n{indent}extra_objects = _extra_objs,\n{indent}extra_link_args = extra_link,\n")]


def summary(edits, match, params) -> str:
    return (f"setup.py unpacks lib{match.params['name']}.a at build time, links its objects with a hidden "
            f"{'/'.join(match.params['symbols'])} shim and -Wl,-Bsymbolic, and drops {match.params['name']} from libs")


register(Recipe(
    ID, "verified-template",
    "Rewrites setup.py to unpack the non-PIC lib<X>.a, link its objects directly with a hidden stderr shim "
    "and -Wl,-Bsymbolic (passed the validator for pybigwig). new_text: empty.",
    recognise, expand, summary))
