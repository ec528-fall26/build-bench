"""disable-lto: a link that only fails because the package is built with link-time optimisation.

globalplatform (noble, amd64): the unit tests are linked with
`-Wl,--wrap=RAND_bytes` and a `__wrap_RAND_bytes` defined in a test file.
Ubuntu's dpkg-buildflags adds -flto=auto -ffat-lto-objects; with LTO the call
is resolved inside the optimiser's own object and the wrapper is never found:

    /usr/bin/ld: /tmp/ccXXXX.ltrans0.ltrans.o: in function `mutual_authentication.constprop.0':
    crypto.c:2264: undefined reference to `__wrap_RAND_bytes'

arm64 never saw it because debian/rules builds those tests on a list of
architectures that leaves arm64 out. The maintainer answer in Ubuntu is
`optimize=-lto` in DEB_BUILD_MAINT_OPTIONS: a build flag, every test stays.

Verified on 13 November 2026 with a real amd64 build (emulated, noble):
with `optimize=-lto` appended the package builds and 5 of 5 tests pass. In the
rc.5 run the model instead edited the mocking header shared by all tests.

ocaml (oracular, both architectures): 41 tests of the compiler's own suite
fail with "... was expected to be empty because the compiler should not
generate any output ... but it is not", and what is in the file is gcc's
`[-Wlto-type-mismatch]` warning from linking the C stubs with LTO. Verified
with a real arm64 build (53 minutes): with `optimize=-lto` 3546 tests pass,
0 fail.

kissat (plucky, amd64): debian/rules asks for `optimize=+lto`; the solver is
linked with -flto and crashes the first time it is run, when help2man asks it
for --help ("help2man: can't get `--help' info from build/kissat", make Error
139). In rc.5 the model removed the help2man call and the same crash moved to
the tests. Verified with a real amd64 build (emulated): with `optimize=-lto`
the package builds and its test program runs through.

Recognised only for those three signatures: an undefined reference to a
`__wrap_` symbol reported from an `.ltrans` object; a test that fails on
unexpected compiler output next to a `-Wlto-type-mismatch` warning; a program
that help2man cannot get --help from because it dies with signal 11 right
after being linked; in every case in a build that passes -flto. Other LTO symptoms are not matched: the
warning alone also appears in builds that fail for unrelated reasons
(tasmanian, inetutils, libgpuarray), and an undefined reference from an ltrans
object is usually a symbol that really is missing (rapmap).

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, read, register

ID = "disable-lto"
OPTION = "optimize=-lto"
_WRAP = re.compile(r"undefined reference to [`'‘](?P<symbol>__wrap_\w+)['’]")
_LTRANS = re.compile(r"\.ltrans\d*\.(?:ltrans\.)?o\b")
_UNEXPECTED_OUTPUT = re.compile(r"expected to be empty|should not (?:generate|produce) any output")
_NO_HELP = re.compile(r"help2man: can't get [`'‘]--(?:help|version)['’] info from (?P<program>\S+)")
_SIGSEGV = re.compile(r"Error 139\b|Segmentation fault|signal 11\b|exit status:? 139\b")
_MISMATCH = re.compile(r"\[-Wlto-type-mismatch\]")
NEAR = 12             # lines after the failing test's message in which the warning must appear
_FLTO = re.compile(r"(?<![\w-])-flto(?:=\w+)?(?![\w-])")
_OPTIONS = re.compile(r"^(?P<head>(?:export[ \t]+)?DEB_BUILD_MAINT_OPTIONS[ \t]*[:?]?=)(?P<value>[^\n\\]*)\n",
                      re.MULTILINE)


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    text = [line if isinstance(line, str) else "" for line in log_lines]
    hit = None
    for index, line in enumerate(text):
        found = _WRAP.search(line)
        if found and any(_LTRANS.search(near) for near in text[max(0, index - 2):index + 1]):
            hit = (index, found.group("symbol"), "wrap")
            break
        if _UNEXPECTED_OUTPUT.search(line) and any(_MISMATCH.search(near) for near in text[index:index + NEAR]):
            hit = (index, "", "output")
            break
        crashed = _NO_HELP.search(line)
        if crashed and any(_SIGSEGV.search(near) for near in text[index:index + 4]):
            hit = (index, crashed.group("program"), "startup")
            break
    if hit is None or not any(_FLTO.search(line) for line in text[:hit[0]]):
        return None
    rules = read(worktree, "input/debian/rules")
    if not rules or OPTION in rules:
        return None
    if hit[2] == "wrap":
        count = sum(1 for line in text if _WRAP.search(line))
        note = f"({count} such lines; the objects are .ltrans, the linker's LTO partitions)"
    elif hit[2] == "startup":
        name = hit[1].rsplit("/", 1)[-1]
        link = next((line for line in text[:hit[0]] if _FLTO.search(line)
                     and re.search(r"\s-o\s+(?:\S*/)?" + re.escape(name) + r"(?:\s|$)", line)), None)
        if link is None:
            return None                     # the program was not linked with LTO in this log
        crash = next(near for near in text[hit[0]:hit[0] + 4] if _SIGSEGV.search(near))
        note = f"({crash.strip()[:120]}; it was linked with: {link.strip()[:110]} ...)"
    else:
        count = sum(1 for line in text if _UNEXPECTED_OUTPUT.search(line))
        warning = next(near for near in text[hit[0]:hit[0] + NEAR] if _MISMATCH.search(near))
        note = f"({count} tests fail this way; the unexpected output is: {warning.strip()[:160]})"
    return Match(ID, evidence=[text[hit[0]].strip()[:240], note],
                 params={"symbol": hit[1], "kind": hit[2]}, line=hit[0])


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    if OPTION in text:
        raise Refusal(f"debian/rules already has {OPTION}; the failure has another cause.")
    lines = text.split("\n")
    existing = [found for found in _OPTIONS.finditer(text)
                if rules_mk.conditional_depth(lines, text[:found.start()].count("\n")) == 0]
    if existing:
        found = existing[0]
        if text.count(found.group(0)) != 1:
            raise Refusal("the DEB_BUILD_MAINT_OPTIONS line is not unique in debian/rules.")
        value = found.group("value").rstrip()
        value = re.sub(r"(?<![\w=,-])optimize=\+lto\b", OPTION, value) if "optimize=+lto" in value \
            else f"{value} {OPTION}" if value.strip() else f" {OPTION}"
        new = f"{found.group('head')}{value}\n"
        if not found.group("head").startswith("export") and not re.search(
                r"^export[ \t]+(?:\w+[ \t]+)*DEB_BUILD_MAINT_OPTIONS\b|^export[ \t]*$", text, re.MULTILINE):
            new = "export " + new
        match.params["_report"] = f"appended {OPTION} to the existing DEB_BUILD_MAINT_OPTIONS line"
        return [Edit(path, found.group(0), new)]
    add = ("# Fails only with link-time optimisation\n"
           f"export DEB_BUILD_MAINT_OPTIONS = {OPTION}\n")
    old, new = rules_mk.insertion(text, add)
    rules_mk.checked(text, old, new, tokens=(OPTION,))
    match.params["_report"] = f"exported DEB_BUILD_MAINT_OPTIONS = {OPTION}"
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    return match.params.get("_report", f"added {OPTION}")


register(Recipe(
    ID, "verified",
    "Adds optimize=-lto to DEB_BUILD_MAINT_OPTIONS in debian/rules, for a link that reports an undefined "
    "reference to a __wrap_ symbol from an .ltrans object, or tests that fail on unexpected compiler output "
    "that is an LTO type-mismatch warning, or a program linked with -flto that crashes when help2man "
    "runs it. Verified with real builds of globalplatform, ocaml and kissat. "
    "new_text: empty.",
    recognise, expand, summary))
