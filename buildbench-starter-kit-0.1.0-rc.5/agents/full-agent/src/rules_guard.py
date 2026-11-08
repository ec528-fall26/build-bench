"""Refuse edits that would break the competition rules or the source package.

rules.html section 6: "Do not suppress failures, delete essential tests, ...
or disable meaningful functionality merely to obtain a successful build
result", and section 9.5: build bypass may disqualify. The prompts say so, but
in the 0.3.3 baseline on the 111 small public cases (6 November) the model
still deleted the failing assertions (lua-struct), made three tests return
early on aarch64 (libapache2-mod-auth-openidc), skipped tests
(python-pyppmd) and added "|| true" (node-re2). A prompt cannot guarantee
compliance, so each edit is checked before it is applied, and a refused edit
is dropped with the reason, which goes back to the model with the attempt's
result.

Also refused, because they can never produce a valid source package: edits
under .pc/ (quilt's record of applied patches; pyfftw) and under
debian/patches/ (dpkg-source refuses a hand-edited patch; pybigwig). A change
to the patched source is recorded as a patch automatically.

The checks look only at the edit itself (old_text against new_text), so they
are cheap and never read the worktree. They are deliberately narrow: a
legitimate fix of a test's expectation keeps its assertions and is allowed.

Standard library only.
"""

from __future__ import annotations

import re

_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|t|testsuite|testing|unit_?tests?|spec|check)(?:/|$)"
    r"|(?:^|/)test[^/]*$|[_-]tests?\.[A-Za-z]+$|\.t$|_spec\.[A-Za-z]+$", re.IGNORECASE)
_ASSERT = re.compile(
    r"\b(?:assert\w*|ASSERT_\w+|EXPECT_\w+|CHECK(?:_\w+)?|REQUIRE(?:_\w+)?|"
    r"munit_assert\w*|g_assert\w*|ck_assert\w*|TEST_ASSERT\w*|CU_ASSERT\w*|"
    r"is_deeply|cmp_ok|isnt|ok|is|like|throws_ok|dies_ok|lives_ok|"
    r"assertEqual|assertTrue|assertRaises|self\.fail)\s*[(!]"
    r"|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(", re.MULTILINE)
_ARCH = re.compile(
    r"__aarch64__|__arm__|__ARM_\w+|__x86_64__|__amd64__|__i386__|_M_X64|_M_ARM64|"
    r"target_arch|platform\.machine|os\.uname|uname\s+-m|runtime\.GOARCH|DEB_HOST_ARCH\w*|"
    r"\b(?:aarch64|arm64|x86_64|amd64)\b")
_SKIP = re.compile(
    r"\bskip\w*\s*\(|pytest\.mark\.(?:skip|xfail)|@unittest\.(?:skip|expectedFailure)|"
    r"\bt\.Skip\w*\s*\(|#\[ignore\]|\bplan\s+skip_all|\bskip_all\b|\bSKIP:|"
    r"\bexit\s+77\b|\bXFAIL_TESTS\b|\bexpectedFailure\b", re.IGNORECASE)
_EARLY_RETURN = re.compile(r"^\s*return\b[^;\n]*;?\s*$|^\s*(?:return|exit)\s*$", re.MULTILINE)

# debian/rules: ways to make a failing command pass.
_RULES_SUPPRESS = (
    (re.compile(r"\|\|\s*(?:true|:|exit\s+0|/bin/true)\b|\|\|\s*:\s*$", re.MULTILINE),
     'it appends "|| true" (or an equivalent) so that a failing command passes'),
    (re.compile(r"^\t\s*-\s*(?:dh_auto_test|\$\(MAKE\)|make\b|ctest|pytest|prove|"
                r"python\S*\s+-m\s+(?:pytest|unittest)|[^\n]*\b(?:test|check)\b)", re.MULTILINE),
     'it prefixes a recipe command with "-" so that make ignores its failure'),
    (re.compile(r"(?:DEB_BUILD_OPTIONS|DEB_BUILD_PROFILES)\s*[:+?]?=.*\bnocheck\b|"
                r"\bnocheck\b.*(?:export|:=|\+=)", re.MULTILINE),
     "it sets nocheck, which turns the test suite off"),
    (re.compile(r"-k\s*['\"]?\s*not\b|--deselect\b|--ignore(?:-glob)?[= ]|"
                r"--exclude-tests?\b|\bctest\b[^\n]*\s-E\s|--gtest_filter=-", re.MULTILINE),
     "it deselects failing tests from the test run"),
    (re.compile(r"\bexit\s+0\b", re.MULTILINE),
     'it adds "exit 0", which ends a step as successful whatever happened'),
)
_TEST_LIST = re.compile(
    r"\badd_test\s*\(|\bTESTS\s*\+?=|\bcheck_PROGRAMS\b|\btest\s*\(\s*['\"]|"
    r"\bAT_SETUP\b|\bm4_include\s*\(\s*\[?[\w./-]*test", re.IGNORECASE)
_LIST_FILE = re.compile(r"(?:^|/)(?:Makefile\.am|Makefile\.in|meson\.build|CMakeLists\.txt|"
                        r"[\w.-]+\.at|testsuite\.\w+)$")


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _added(old: str, new: str) -> list[str]:
    before = set(_lines(old))
    return [line for line in _lines(new) if line not in before]


def _removed(old: str, new: str) -> list[str]:
    after = set(_lines(new))
    return [line for line in _lines(old) if line not in after]



# Match explicit build producers, not arbitrary recipe commands or clean steps.
_BUILD_PRODUCER = re.compile(
    r"^(?:python[\w.]*\s+setup\.py\s+(?:build_sphinx|build|build_ext)\b|"
    r"(?:sphinx-build|dh_auto_build|cmake\s+--build)\b|"
    r"(?:\$\(MAKE\)|make|ninja)\s+(?:-C\s+\S+\s+)?(?:html|all|build)\b)")
_NOOP = re.compile(r"^(?::|true|/bin/true)(?:\s*;)?(?:\s+#.*)?$")


def _recipe_commands(text: str) -> list[str]:
    """Read simple edit fragments; leave ambiguous shell/make syntax alone."""
    commands = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.fullmatch(r"[\w%.-]+:[ \t]*", line):
            continue
        commands.append(line.lstrip("@"))
    return commands


def _build_step_bypass(old_text: str, new_text: str) -> str | None:
    before = _recipe_commands(old_text)
    after = _recipe_commands(new_text)
    if not after or not all(_NOOP.fullmatch(line) for line in after):
        return None
    if not any(_BUILD_PRODUCER.match(line) for line in before):
        return None
    return ("it replaces a build-producing command with a no-op. Preserve the "
            "required outputs: inspect the packaging manifests and build configuration, "
            "then repair or replace the producer with a working equivalent")


def check(path: str, old_text: str, new_text: str) -> str | None:
    """Why this edit is refused, or None when it may be applied."""
    relative = path[len("input/"):] if path.startswith("input/") else path
    if relative.startswith(".pc/") or "/.pc/" in relative:
        return ("files under .pc/ are quilt's record of the applied patches, not source; "
                "edit the patched file itself")
    if relative.startswith("debian/patches/"):
        return ("a hand-edited file under debian/patches/ makes dpkg-source reject the "
                "package; edit the patched source under input/ instead, and the change is "
                "recorded as a patch automatically")
    added = _added(old_text, new_text)
    removed = _removed(old_text, new_text)
    added_text = "\n".join(added)

    if relative == "debian/rules" or relative.endswith("/debian/rules"):
        bypass = _build_step_bypass(old_text, new_text)
        if bypass:
            return bypass
        for regex, why in _RULES_SUPPRESS:
            if regex.search(added_text) and not regex.search(old_text):
                return why + "; the rules forbid suppressing failures to obtain a build"
        if (re.search(r"^override_dh_auto_test\w*:", new_text, re.MULTILINE)
                and not re.search(r"^override_dh_auto_test\w*:", old_text, re.MULTILINE)):
            body = re.search(r"^override_dh_auto_test\w*:[^\n]*\n((?:\t[^\n]*\n?)*)",
                             new_text, re.MULTILINE)
            recipe = body.group(1) if body else ""
            if not re.search(r"dh_auto_test|\b(?:make|ctest|pytest|prove|go test|cargo test|"
                             r"meson test|ninja test)\b|\bcheck\b|\btest\b", recipe):
                return ("it overrides dh_auto_test with a recipe that does not run the "
                        "tests; the rules forbid turning tests off to obtain a build")
        return None

    if _LIST_FILE.search(relative):
        gone = [line for line in removed if _TEST_LIST.search(line)]
        if gone and len(_TEST_LIST.findall(new_text)) < len(_TEST_LIST.findall(old_text)):
            return ("it removes tests from the build's test list; the rules forbid deleting "
                    "tests to obtain a build")
        return None

    if _TEST_PATH.search(relative):
        before = len(_ASSERT.findall(old_text))
        after = len(_ASSERT.findall(new_text))
        if after < before:
            return (f"it removes {before - after} test assertion(s) from a test file; the "
                    "rules forbid deleting tests or their checks to obtain a build. Fix the "
                    "code under test, or correct the expected value and keep the check")
        if _SKIP.search(added_text) and not _SKIP.search(old_text):
            return ("it skips or expects the failure of a test; the rules forbid disabling "
                    "tests to obtain a build. Fix the cause the test exposes")
        if _EARLY_RETURN.search(added_text) and _ARCH.search(added_text):
            return ("it makes a test return early on one architecture, so the test no longer "
                    "runs there; the rules forbid this. Fix the cause the test exposes")
        return None

    return None
