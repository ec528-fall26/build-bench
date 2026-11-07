"""Name the failing tests and why, when a build fails in its test suite.

Across the 200 public development cases, 59 fail while running the package's
own tests (the largest single stage). The diagnostics for those are wrapper
lines ("dh_auto_test: error: ... returned exit code 13"), which say that tests
failed but not which test or why, so the model guesses. The rules forbid the
shortcut (rules.html section 6: do not suppress failures or delete essential
tests), so a repair has to fix the architecture assumption the test exposes,
and that needs the failing test and its assertion.

This module finds the test step's output in the log, extracts each failing
test with its message from the common frameworks (Python unittest and pytest,
automake and Autotest, CTest and GoogleTest, go test, cargo test, TAP, Perl,
meson, Haskell), keeps crash and assertion lines a framework parser would
miss, resolves the test's source file in the worktree and renders one block
for the opening message. It is evidence; nothing is edited by it.

Standard library only; every scan is bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re

from .failure import classify_failure
from .tools import collect_files

MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_REGION_LINES = 40_000
MAX_FAILURES = 8
MAX_DETAIL_LINES = 12
MAX_OTHER_LINES = 10
MAX_SNIPPETS = 3
SNIPPET_CONTEXT = 8
LINE_CHARS = 220
MAX_BLOCK_CHARS = 9000

TITLE = "=== Test-suite failure (evidence from the log) ==="
END = "=== End of test-suite failure ==="

_PREFIX = re.compile(r"^\[\s*\d+s\] ")  # the validator's elapsed-time prefix
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BUILDDIR = re.compile(r"^/(?:<<PKGBUILDDIR>>|build/[^/]+/[^/]+|tmp/[^/]+)/")

# Where the test step fails and where it starts.
_TEST_ERROR = re.compile(
    r"dh_auto_test: (?:error|.*returned exit code)|override_dh_auto_test\] Error \d|"
    r"make(?:\[\d+\])?: \*\*\* \[[^\]]*(?:check|test)[^\]]*\] Error \d")
_KILLED = re.compile(r"^E: Build killed with signal \w+ after \d+ minutes of inactivity")
_NOISE = re.compile(r"^(?:make(?:\[\d+\])?: |dh_\w+: |\s*$|dpkg-buildpackage: |-{20,})")
_TEST_START = re.compile(r"^\s*(?:dh_auto_test\b|debian/rules override_dh_auto_test\b)")


@dataclass
class TestFailure:
    framework: str
    name: str
    details: list[str] = field(default_factory=list)
    locations: list[tuple[str, int]] = field(default_factory=list)


def _clean(raw: str) -> str:
    return _ANSI.sub("", _PREFIX.sub("", raw)).rstrip()


def read_log(log_path: Path | None) -> list[str]:
    if log_path is None:
        return []
    try:
        with open(log_path, "rb") as handle:
            data = handle.read(MAX_LOG_BYTES)
    except OSError:
        return []
    return [_clean(line) for line in data.decode("utf-8", "replace").splitlines()]


def test_region(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) of the test step's output; end is the failing test-step line.

    A build killed for inactivity while the tests run (a hung test) counts too,
    but only when a test step had started: a hang elsewhere is not a test failure.
    """
    end = next((i for i, line in enumerate(lines) if _TEST_ERROR.search(line)), None)
    killed = next((i for i, line in enumerate(lines) if _KILLED.match(line)), None)
    if killed is not None and (end is None or killed < end):
        start = next((i for i in range(killed - 1, -1, -1) if _TEST_START.search(lines[i])), None)
        if start is not None:
            return max(start, killed - MAX_REGION_LINES), killed
    if end is None:
        return None
    start = next((i for i in range(end - 1, -1, -1) if _TEST_START.search(lines[i])), None)
    if start is None or end - start > MAX_REGION_LINES:
        start = max(0, end - MAX_REGION_LINES)
    return start, end


def end_of_output(region: list[str], count: int = 5) -> list[str]:
    """The last lines the tests printed, without the make/debhelper error chain."""
    kept = []
    for line in reversed(region[:-1]):
        if _NOISE.match(line) or "Entering directory" in line or "Leaving directory" in line:
            if kept:
                break
            continue
        kept.append(line)
        if len(kept) == count:
            break
    return list(reversed(kept))


def _short(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + " [cut]"


# --- frameworks -----------------------------------------------------------

_PY_HEADER = re.compile(r"^(FAIL|ERROR): (\S+) \((\S+?)\)")
_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_PY_RULE = re.compile(r"^(?:={20,}|-{20,})$")


def _python_unittest(region: list[str]) -> list[TestFailure]:
    found = []
    for i, line in enumerate(region):
        match = _PY_HEADER.match(line)
        if not match:
            continue
        body = []
        for follow in region[i + 1:i + 200]:
            # The rule right after the header opens the block; the next one closes it.
            if (_PY_RULE.match(follow) and body) or _PY_HEADER.match(follow):
                break
            if not _PY_RULE.match(follow):
                body.append(follow)
        locations = [(p, int(n)) for p, n in _PY_FRAME.findall("\n".join(body))]
        # The last lines of a traceback hold the assertion; frames in test files first.
        tail = [b for b in body if b.strip()][-MAX_DETAIL_LINES:]
        tests = [loc for loc in locations if "test" in loc[0].lower()]
        found.append(TestFailure("python-unittest", f"{match.group(3)}",
                                 tail, (tests or locations)[-2:]))
    return found


_PYTEST_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)(?: - (.*))?$")
_PYTEST_E = re.compile(r"^E\s{2,}\S")
_PYTEST_LOC = re.compile(r"^(\S+\.py):(\d+): \w")


def _pytest(region: list[str]) -> list[TestFailure]:
    names = []
    for line in region:
        match = _PYTEST_SUMMARY.match(line)
        if match and match.group(1) not in [n for n, _ in names]:
            names.append((match.group(1), match.group(2) or ""))
    found = []
    for name, message in names:
        leaf = name.split("::")[-1].split("[")[0]
        header = next((i for i, line in enumerate(region)
                       if re.match(r"^_{3,} .*\b" + re.escape(leaf) + r"\b.* _{3,}$", line)), None)
        details, locations = [], []
        if header is not None:
            block = []
            for follow in region[header + 1:header + 300]:
                if re.match(r"^_{3,} \S.* _{3,}$", follow) or re.match(r"^={3,} ", follow):
                    break
                block.append(follow)
            details = [b for b in block if _PYTEST_E.match(b) or _PYTEST_LOC.match(b)][-MAX_DETAIL_LINES:]
            locations = [(p, int(n)) for b in block for p, n in _PYTEST_LOC.findall(b)][-2:]
        if not details and message:
            details = [message]
        path = name.split("::")[0]
        found.append(TestFailure("pytest", name, details, locations or [(path, 0)]))
    return found


_AUTOMAKE = re.compile(r"^(?:FAIL|ERROR): (\S+)$|^(?:FAIL|ERROR) (\S+) \(exit status: \d+\)$")
_AUTOTEST = re.compile(r"^\s*(\d+): (.+?)\s+FAILED \(([^():]+):(\d+)\)")


def _automake(region: list[str]) -> list[TestFailure]:
    found, seen = [], set()
    for line in region:
        match = _AUTOMAKE.match(line)
        if match:
            name = match.group(1) or match.group(2)
            if name not in seen and not name.startswith("("):
                seen.add(name)
                found.append(TestFailure("automake", name, [], [(name, 0)]))
        match = _AUTOTEST.match(line)
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            found.append(TestFailure("autotest", f"{match.group(1)}: {match.group(2)}", [line.strip()],
                                     [(match.group(3), int(match.group(4)))]))
    return found


_CTEST_LIST = re.compile(r"^\s*(\d+) - (\S+) \((\w[\w ]*)\)")
_GTEST_FAILED = re.compile(r"^(?:\d+: )?\[  FAILED  \] (\S+?\.\S+)")
_GTEST_DETAIL = re.compile(r"Failure$|Expected|Which is|Value of|Actual|to be equal|^\S+:\d+: error")
_C_LOC = re.compile(r"((?:/<<PKGBUILDDIR>>)?[\w./+-]+\.(?:c|cc|cpp|cxx|h|hpp)):(\d+)")


def _ctest_gtest(region: list[str]) -> list[TestFailure]:
    found = []
    try:
        listing = next(i for i, line in enumerate(region) if "The following tests FAILED" in line)
    except StopIteration:
        listing = None
    if listing is not None:
        for line in region[listing + 1:listing + 60]:
            match = _CTEST_LIST.match(line)
            if not match:
                if line.strip():
                    continue
                break
            number, name = match.group(1), match.group(2)
            output = [l[len(number) + 2:] for l in region if l.startswith(number + ": ")]
            details = [l for l in output if _GTEST_DETAIL.search(l) or "FAILED" in l or "rror" in l]
            locations = [(p, int(n)) for l in details for p, n in _C_LOC.findall(l)][:2]
            found.append(TestFailure("ctest", f"{name} ({match.group(3)})",
                                     (details or output[-6:])[:MAX_DETAIL_LINES], locations))
    names = {f.name.split(" ")[0] for f in found}
    for i, line in enumerate(region):
        match = _GTEST_FAILED.match(line)
        if not match or match.group(1) in names or " listed below" in line:
            continue
        names.add(match.group(1))
        start = next((j for j in range(i - 1, max(-1, i - 200), -1)
                      if "[ RUN      ] " + match.group(1) in region[j]), max(0, i - 20))
        details = [l for l in region[start:i] if _GTEST_DETAIL.search(l)]
        locations = [(p, int(n)) for l in details for p, n in _C_LOC.findall(l)][:2]
        found.append(TestFailure("googletest", match.group(1), details[:MAX_DETAIL_LINES], locations))
    return found


_GO_FAIL = re.compile(r"^(\s*)--- FAIL: (\S+)")
_GO_LOC = re.compile(r"(\w+_test\.go):(\d+)")


def _go(region: list[str]) -> list[TestFailure]:
    found = []
    for i, line in enumerate(region):
        match = _GO_FAIL.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        details = []
        for follow in region[i + 1:i + 40]:
            stripped = follow.strip()
            if (re.match(r"^\s*(?:=== RUN|--- (?:FAIL|PASS|SKIP)|FAIL|PASS|ok\s)", follow)
                    and len(follow) - len(follow.lstrip()) <= indent):
                break
            if stripped:
                details.append(stripped)
        # go test prints a test's log above --- FAIL when run with -v
        if not details:
            details = [l.strip() for l in region[max(0, i - 15):i] if _GO_LOC.search(l)]
        locations = [(p, int(n)) for l in details for p, n in _GO_LOC.findall(l)][:2]
        found.append(TestFailure("go-test", match.group(2), details[:MAX_DETAIL_LINES], locations))
    return found


_CARGO_BLOCK = re.compile(r"^---- (\S+) stdout ----$")
_RUST_LOC = re.compile(r"panicked at (?:'.*?', )?([\w./-]+\.rs):(\d+)")


def _cargo(region: list[str]) -> list[TestFailure]:
    found = []
    for i, line in enumerate(region):
        match = _CARGO_BLOCK.match(line)
        if not match:
            continue
        details = []
        for follow in region[i + 1:i + 30]:
            if not follow.strip() or _CARGO_BLOCK.match(follow):
                break
            details.append(follow.strip())
        locations = [(p, int(n)) for l in details for p, n in _RUST_LOC.findall(l)][:2]
        found.append(TestFailure("cargo-test", match.group(1), details[:MAX_DETAIL_LINES], locations))
    return found


_TAP_NOT_OK = re.compile(r"^\s*not ok \d+(?:\s*-\s*|\s+)(.*?)(?:\s+#\s*(TODO|SKIP).*)?$")
_PERL_FAILED = re.compile(r"^#\s+Failed test (?:'(.+?)'|(.+))$")
_PERL_AT = re.compile(r"^#\s+(?:at|in) (\S+) (?:at )?line (\d+)\.")


def _tap_perl(region: list[str]) -> list[TestFailure]:
    found = []
    for i, line in enumerate(region):
        match = _PERL_FAILED.match(line)
        if match:
            name = match.group(1) or match.group(2).strip()
            details, locations = [], []
            for follow in region[i + 1:i + 15]:
                if not follow.startswith("#") or _PERL_FAILED.match(follow):
                    break
                at = _PERL_AT.match(follow)
                if at:
                    locations.append((at.group(1), int(at.group(2))))
                details.append(follow.lstrip("# ").rstrip())
            found.append(TestFailure("perl-test", name, details[:MAX_DETAIL_LINES], locations))
            continue
        match = _TAP_NOT_OK.match(line)
        if match and not match.group(2):
            details = [f.lstrip("# ").rstrip() for f in region[i + 1:i + 12] if f.lstrip().startswith("#")]
            found.append(TestFailure("tap", match.group(1) or line.strip(), details[:MAX_DETAIL_LINES], []))
    return found


_LIT_LIST = re.compile(r"^\s*(?:FAIL|XPASS|TIMEOUT): (\S+ :: \S+)")
_LIT_HEADER = re.compile(r"^(?:Failed|Unexpectedly Passed|Timed Out) Tests \(\d+\):")
_MESON = re.compile(r"^\s*\d+/\d+ (?:[\w-]+ ?/ )?(\S.*?)\s+(FAIL|TIMEOUT|ERROR)\s")
_HASKELL = re.compile(r"^\s+(\S.*?):\s+FAIL\b|^Test suite (\S+): FAIL")


def _meson_haskell(region: list[str]) -> list[TestFailure]:
    found, seen = [], set()
    listed = False
    for line in region:
        if _LIT_HEADER.match(line):
            listed = True
            continue
        match = _LIT_LIST.match(line) or (listed and re.match(r"^\s+(\S+ :: \S+)\s*$", line))
        if match:
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                found.append(TestFailure("llvm-lit", name, [], [(name.split(" :: ")[-1], 0)]))
            continue
        listed = listed and bool(line.strip())
        for framework, regex in (("meson", _MESON), ("haskell", _HASKELL)):
            match = regex.match(line)
            if match:
                name = next(g for g in match.groups() if g)
                if name not in seen:
                    seen.add(name)
                    found.append(TestFailure(framework, name.strip(), [], []))
    return found


FRAMEWORKS = (_python_unittest, _pytest, _automake, _ctest_gtest, _go, _cargo, _tap_perl,
              _meson_haskell)

# Lines worth showing whatever the framework: crashes, assertions, a tool's own fix.
_SALIENT = re.compile(
    r"Segmentation fault|core dumped|Aborted|Illegal instruction|Bus error|"
    r"killed by signal|terminate called|stack smashing|"
    r"[Aa]ssertion .{0,80}failed|assert(?:ion)? failed|AssertionError|panicked at|"
    r"^\S+:\d+: (?:error|Failure)|^Error: \S+:\d+:|"
    r"\bexpected\b.{0,60}\b(?:got|but|actual|found)\b|"
    r"You must (?:patch|add|set|use)|tests? (?:were )?run,\s*$|\d+ failed unexpectedly|"
    r"^testsuite: [\d ]+ failed|FAILED \(failures=|\d+ tests? failed|"
    r"Build killed with signal|timed out|Timeout|"
    r"differ: (?:byte|char) \d|unknown mnemonic|no such instruction|file format not recognized|"
    r"Wstat: \d+ \(Signal|Non-zero (?:wait|exit) status|^\s*Failed\s*:\s*[1-9]")


_ANY_LOC = re.compile(r"((?:/<<PKGBUILDDIR>>)?[\w./+-]+\.(?:c|cc|cpp|cxx|h|hpp|py|lua|go|rs|pl|pm|t|sh|rb|js|ml|hs|at|java|R)):(\d+)")


def salient_lines(region: list[str], offset: int) -> list[tuple[int, str]]:
    picked = []
    for i, line in enumerate(region):
        if _SALIENT.search(line):
            text = line.strip()
            if text and all(text != t for _, t in picked):
                picked.append((offset + i + 1, text))
    # The end of the output is where the decisive lines usually are.
    return picked[-MAX_OTHER_LINES:]


def last_passing(region: list[str], before: int) -> str:
    """For a crash: the last test reported as passing before line ``before``."""
    for line in reversed(region[max(0, before - 400):before]):
        if re.match(r"^\s*(?:ok \d+|\[\s+OK \]|PASS:|--- PASS:|test \S+ \.\.\. ok)", line):
            return line.strip()
    return ""


def extract(lines: list[str]) -> tuple[tuple[int, int] | None, list[TestFailure], list[tuple[int, str]]]:
    region_bounds = test_region(lines)
    if region_bounds is None:
        return None, [], []
    start, end = region_bounds
    region = lines[start:end + 1]
    failures: list[TestFailure] = []
    for framework in FRAMEWORKS:
        try:
            failures.extend(framework(region))
        except Exception:  # evidence must never break a run
            continue
    return region_bounds, failures, salient_lines(region, start)


# --- the worktree ---------------------------------------------------------

def _relative(printed: str) -> str:
    path = _BUILDDIR.sub("", printed.strip())
    return path.lstrip("./")


def resolve(printed: str, files: list[str]) -> str | None:
    """The worktree path (input/...) a printed test path or name refers to, if unique."""
    rel = _relative(printed)
    if not rel or rel.startswith("/usr/"):
        return None
    candidates = []
    if "/" in rel or "." in PurePosixPath(rel).name:
        candidates = [f for f in files if f == "input/" + rel or f.endswith("/" + rel)]
    if not candidates and re.fullmatch(r"[\w.]+", rel) and rel.count(".") >= 1:
        # a dotted Python test id: package.tests.test_mod.Class.test_name
        parts = rel.split(".")
        for cut in range(len(parts), 0, -1):
            module = "/".join(parts[:cut]) + ".py"
            candidates = [f for f in files if f.endswith("/" + module)]
            if candidates:
                break
    if not candidates:
        name = PurePosixPath(rel).name
        script = [f for f in files if PurePosixPath(f).name == name]
        if len(script) == 1:
            candidates = script
    return candidates[0] if len(candidates) == 1 else None


def _snippet(worktree: Path, path: str, line: int, name: str) -> str:
    try:
        text = (worktree / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if line <= 0:
        leaf = re.split(r"[.:/ ]", name.split("::")[-1].split("[")[0])[-1]
        line = next((n for n, l in enumerate(text, 1)
                     if leaf and re.search(r"\b(?:def|func|fn|sub|test_?)\s*\(?\s*" + re.escape(leaf) + r"\b", l)), 0)
        if line == 0:
            line = 1
    lo, hi = max(1, line - SNIPPET_CONTEXT), min(len(text), line + SNIPPET_CONTEXT)
    body = "\n".join(f"{n:5d}{'>' if n == line else ' '} {_short(text[n - 1])}" for n in range(lo, hi + 1))
    return f"--- {path}, lines {lo}-{hi} ---\n{body}"


def render(log_path: Path | None, worktree: Path | None, target_arch: str = "") -> str:
    """One block for the opening message, or "" when the log shows no test-step failure."""
    try:
        # Only when the build failed in its tests: Debian's Rust packaging, for
        # one, compiles inside the test step, and a compile error is not a test.
        if log_path is None or classify_failure(Path(log_path)).stage != "test":
            return ""
        lines = read_log(log_path)
        bounds, failures, salient = extract(lines)
    except Exception:
        return ""
    if bounds is None:
        return ""
    start, end = bounds
    files: list[str] = []
    if worktree is not None and (Path(worktree) / "input").is_dir():
        try:
            found, _ = collect_files(Path(worktree) / "input")
            # .pc/ holds quilt's copies of files before patching: never the file to read
            # or edit, and a second match would make the real test file ambiguous.
            files = sorted(rel for rel in (p.relative_to(worktree).as_posix() for p in found)
                           if "/.pc/" not in rel)
        except OSError:
            files = []

    out = [TITLE, f"The build failed while running the package's own tests "
                  f"(log lines {start + 1}-{end + 1}). "
                  + (f"{len(failures)} failing test(s) found:" if failures else
                     "No test framework report was recognised; the decisive lines are below.")]
    snippets, shown = [], set()
    for number, failure in enumerate(failures[:MAX_FAILURES], 1):
        out.append(f"{number}. [{failure.framework}] {_short(failure.name)}")
        out.extend("     " + _short(d) for d in failure.details[:MAX_DETAIL_LINES])
        # The deepest frame first: it is where the assertion fired.
        for printed, line in list(reversed(failure.locations)) or [(failure.name, 0)]:
            path = resolve(printed, files) if files else None
            if path:
                out.append(f"     source: {path}" + (f" line {line}" if line else ""))
                if path not in shown and len(snippets) < MAX_SNIPPETS:
                    shown.add(path)
                    snippets.append(_snippet(Path(worktree), path, line, failure.name))
                break
    if len(failures) > MAX_FAILURES:
        out.append(f"... and {len(failures) - MAX_FAILURES} more failing tests.")
    if _KILLED.match(lines[end]):
        last = end_of_output(lines[start:end + 1], 1)
        out.append("The build was killed because the test suite stopped producing output (a "
                   "hung or deadlocked test). The test running at that point is the one "
                   "after the last line it printed"
                   + (f": {_short(last[0].strip())}" if last else "") + ".")
    tail = end_of_output(lines[start:end + 1])
    if tail:
        out.append(f"End of the test output (before line {end + 1}):")
        out.extend("  " + _short(t) for t in tail)
    if salient:
        out.append("Crash, assertion and summary lines in the test output:")
        for number, text in salient:
            out.append(f"  line {number}: {_short(text)}")
        crash = next((n for n, t in salient if re.search(r"Segmentation|core dumped|Aborted|Illegal|Bus error", t)), None)
        if crash is not None:
            previous = last_passing(lines, crash - 1)
            if previous:
                out.append(f"  the last test reported as passing before the crash: {_short(previous)}")
    # file:line in a crash or assertion line names the test source too.
    for _, text in reversed(salient):
        if len(snippets) >= MAX_SNIPPETS or not files:
            break
        for printed, line in _ANY_LOC.findall(text):
            path = resolve(printed, files)
            if path and path not in shown:
                shown.add(path)
                snippets.append(_snippet(Path(worktree), path, int(line), ""))
                break
    out.extend(s for s in snippets if s)
    arch = target_arch or "the target architecture"
    out.append(
        f"How to repair a test that fails only on {arch}: the competition rules forbid "
        "disabling, skipping or deleting the test, or ignoring its failure. Read the assertion "
        "(expected against actual) and find the architecture assumption it exposes, for example: "
        "exact floating-point results or printed precision (x87/FMA/long double differ), char "
        "signedness (char is unsigned on arm64, signed on x86_64), byte order or word size, page "
        "size, alignment, an x86-only CPU feature or instruction set, an expected output recorded "
        "on x86, a timeout tuned for one machine, or uninitialised memory that happens to work on "
        "one architecture. Fix the code when it is wrong on the target; when the test's "
        "expectation itself is architecture-specific, make it correct for every architecture (a "
        "tolerance, the portable type, a per-architecture expected value) and say why in a "
        "comment. If a tool in the test step prints the change it needs, make that change.")
    out.append(END)
    text = "\n".join(out)
    if len(text) > MAX_BLOCK_CHARS:
        text = text[:MAX_BLOCK_CHARS] + "\n[cut]\n" + END
    return text
