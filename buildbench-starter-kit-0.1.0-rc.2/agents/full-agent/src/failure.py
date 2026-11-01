"""Part A: at which stage did the build fail, and what supports that?

A failure while the build environment is being prepared cannot be fixed by
editing the package; a compile or link error usually can. The Demo 2 retest
shows why this matters: all five x86_64-target cases failed during setup
(segmentation faults, a failed passwd post-install script, an incomplete
debootstrap root) before any package code was compiled, and the agents spent
model calls editing source anyway.

classify_failure() reads one build log, streaming and bounded, and returns a
FailureStage. It prefers the validator's own structured stage event
("[build-case] [event] {...}") and refines it with messages from the log.
It reports what it saw, not a root cause: a segmentation fault during setup is
recorded as such, without concluding that emulation (QEMU) caused it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from .evidence import _ANSI, _COMMAND, _ELAPSED, _FORTRAN_ERROR, _LINKER

# Where a build can fail, in build order. "none": the build succeeded.
STAGES = ("setup", "dependencies", "configure", "compile", "link", "test",
          "install", "packaging", "none", "unknown")

EVIDENCE_LINES = 8          # supporting log lines kept
EVIDENCE_CHARS = 300        # per supporting line

_EVENT = re.compile(r"^\[build-case\] \[event\] (?P<json>\{.*\})\s*$")
# Validator stages before the package build. A failure in any of them is not
# something an edit to the package can fix, except dependency resolution.
_VALIDATOR_SETUP = {"build_setup", "build_infrastructure", "backend_preflight",
                    "manifest_validation", "workspace"}
_VALIDATOR_DEPENDENCIES = {"dependency_preflight", "dependency_resolution"}
# Logs larger than this are classified from their last TAIL_BYTES: the
# failure, the validator's event and the step markers near it are at the end.
TAIL_BYTES = 32_000_000

# Build steps, Debian (debhelper) and RPM (rpmbuild sections).
_DH_STEP = re.compile(r"\b(?:override_)?(dh_auto_(?:configure|build|test|install))\b")
_DH_PACKAGING = re.compile(r"^\s*(?:dh_(?:install|missing|strip|shlibdeps|gencontrol|builddeb|makeshlibs)|"
                           r"dpkg-(?:deb|gencontrol|shlibdeps|genchanges))\b")
_RPM_STEP = re.compile(r"^\+? ?Executing\((?P<section>%\w+)\)")
# Not "RPM build errors:": rpmbuild prints it after a failure in any section.
_RPM_PACKAGING = re.compile(r"^(?:Processing files:|Wrote: )")
_RPM_BAD_EXIT = re.compile(r"Bad exit status from \S+ \((?P<section>%\w+)\)")
_BUILD_STARTED = re.compile(r"^(?:dpkg-buildpackage: info: source package\b|\+? ?Executing\(%)")

_STEP_STAGE = {
    "dh_auto_configure": "configure", "dh_auto_build": "compile",
    "dh_auto_test": "test", "dh_auto_install": "install",
    "%prep": "configure", "%conf": "configure", "%build": "compile",
    "%check": "test", "%install": "install", "packaging": "packaging",
}

# Messages, by the stage they point to. The first match of a kind is kept.
_SIGNALS = (
    ("dependencies", re.compile(
        r"nothing provides |\bunresolvable\b|have choice for .* \(|Unable to locate package|"
        r"unmet (?:build )?dependencies|Unmet build dependencies|but it is not (?:going to be )?installable|"
        r"Failed (?:build )?dependencies:", re.IGNORECASE)),
    ("setup", re.compile(
        r"Segmentation fault|chroot: (?:failed to run command|can't execute)|"
        r"build-recipe-debootstrap|post-?installation script subprocess returned error|"
        r"postinst maintainer script subprocess failed|Errors were encountered while processing|"
        r"cannot (?:mount|create) .*build root|E: Build killed", re.IGNORECASE)),
    ("configure", re.compile(
        # Not "Could NOT find ...": CMake prints that for optional packages too.
        r"^CMake Error\b|configure: error:|^meson\.build:\d+:\d+: ERROR|^ERROR: Problem encountered|"
        r"dh_auto_configure: error")),
    ("link", re.compile(
        r"undefined reference to|collect2: error: ld returned|cannot find -l\S+|"
        r"relocation .* can not be used when making a shared object|is referenced by DSO|"
        r"multiple definition of")),
    ("test", re.compile(
        # Not "0 tests failed": CTest prints that on success.
        r"^FAIL: |^# FAIL: +[1-9]|\b[1-9]\d* (?:of \d+ )?tests? failed\b|\bsome tests failed\b|"
        r"Errors while running CTest|"
        r"^make(?:\[\d+\])?: \*\*\* \[[^]]*\b(?:check|test)\b", re.IGNORECASE)),
    ("packaging", re.compile(
        r"dh_(?:install|missing): .*(?:missing files|not installed|missing from)|"
        r"Installed \(but unpackaged\) file\(s\) found|^\s*File not found(?: by glob)?:|"
        r"dpkg-(?:deb|gencontrol|shlibdeps): error|dh_builddeb: error")),
)
_COMPILE_ERROR = re.compile(r"^.+?:\d+(?::\d+)?:\s*(?:fatal )?error:|^ERROR: compilation failed for package")
# Coded Rust errors only: a bare "error: ..." line can come from any tool.
_RUST_CODED = re.compile(r"^error\[E\d+\]:")
_SPECIFIC_ORDER = ("configure", "compile", "link", "test", "install", "packaging")
# Most of a large log matches nothing above. Every pattern above contains one of
# these words (matched against the lower-cased line), so lines without any are
# skipped before decoding: logs reach gigabytes.
_PREFILTER = re.compile(
    rb"\[build-case\]|dh_|dpkg-|executing\(%|processing files|wrote: |rpm build|"
    rb"nothing provides|unresolvable|have choice|unable to locate|unmet|installable|dependencies:|"
    rb"segmentation fault|chroot: |debootstrap|installation script|postinst|errors were|"
    rb"build root|build killed|cmake error|meson\.build|problem encountered|undefined reference|"
    rb"collect2|cannot find -l|relocation|referenced by dso|multiple definition|ld(?:\.\w+)?: |"
    rb"fail|ctest|\*\*\*|missing|not installed|unpackaged|file not found|error")


@dataclass
class FailureStage:
    """The stage at which the build failed, with the log lines that support it.

    stage: one of STAGES. source: "validator", "log" or "none" (what decided).
    validator_stage: the validator's own stage name, when the log carries one.
    build_step: the packaging step that was running (e.g. "dh_auto_test",
    "%build"), when known. repairable_in_package: False for setup failures,
    which edits to the package cannot fix; True for stages inside the package
    build; None when unknown or the build succeeded.
    """
    stage: str = "unknown"
    source: str = "none"
    validator_stage: str | None = None
    build_step: str | None = None
    repairable_in_package: bool | None = None
    evidence: list[str] = field(default_factory=list)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EVIDENCE_CHARS else text[:EVIDENCE_CHARS - 1] + "…"


def _repairable(stage: str) -> bool | None:
    if stage in ("setup",):
        return False
    if stage in ("none", "unknown"):
        return None
    return True


def classify_failure(log_path: Path) -> FailureStage:
    """Classify one build log. Reads it once, line by line; never raises.

    Logs over TAIL_BYTES are read from TAIL_BYTES before their end, and line
    numbers in the evidence then count from there (the evidence says so).
    """
    event: tuple[str, str, int] | None = None      # (stage, state, line number)
    step: str | None = None
    started = False
    first: dict[str, tuple[int, str, str | None]] = {}   # kind -> (line, text, step)
    counts: dict[str, int] = {}
    number = 0
    tail_note = None
    try:
        with Path(log_path).open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(0)
            if size > TAIL_BYTES:
                stream.seek(size - TAIL_BYTES)
                stream.readline()          # skip the partial first line
                started = True             # a log this long got past environment setup
                tail_note = (f"only the last {TAIL_BYTES // 1_000_000} MB of this "
                             f"{size // 1_000_000} MB log were read; line numbers count from there")
            for raw in stream:
                number += 1
                if not _PREFILTER.search(raw.lower()):
                    continue
                text = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                found = _EVENT.match(text)
                if found:
                    try:
                        data = json.loads(found.group("json"))
                    except (ValueError, RecursionError):
                        data = None
                    if isinstance(data, dict) and data.get("event") == "stage":
                        event = (str(data.get("stage")), str(data.get("state")), number)
                    continue
                text = _ELAPSED.sub("", text).strip()
                if not text or _COMMAND.match(text):
                    continue   # compiler command lines often contain -Werror=...
                if not started and _BUILD_STARTED.match(text):
                    started = True
                marker = _DH_STEP.search(text)
                if marker:
                    step = marker.group(1)
                rpm = _RPM_STEP.match(text) or _RPM_BAD_EXIT.search(text)
                if rpm:
                    step = rpm.group("section")
                elif _DH_PACKAGING.match(text) or _RPM_PACKAGING.match(text):
                    step = "packaging"

                kinds = [kind for kind, pattern in _SIGNALS if pattern.search(text)]
                if _COMPILE_ERROR.match(text) or _FORTRAN_ERROR.match(text) or _RUST_CODED.match(text):
                    kinds.append("compile")
                if _LINKER.match(text) and "link" not in kinds:
                    kinds.append("link")
                for kind in kinds:
                    counts[kind] = counts.get(kind, 0) + 1
                    if kind not in first:
                        first[kind] = (number, text, step)
    except OSError:
        return FailureStage("unknown", "none", None, None, None, ["log missing or unreadable"])

    evidence: list[str] = [tail_note] if tail_note else []
    validator_stage = event[0] if event else None
    if event:
        evidence.append(f"line {event[2]}: validator stage {event[0]!r} ended {event[1]!r}")

    def support(*kinds: str) -> None:
        for kind in kinds:
            if kind in first and len(evidence) < EVIDENCE_LINES:
                line, text, _ = first[kind]
                times = counts.get(kind, 1)
                suffix = f" (seen {times} times)" if times > 1 else ""
                evidence.append(_clip(f"line {line}: {text}{suffix}"))

    def result(stage: str, source: str, build_step: str | None, note: str | None = None) -> FailureStage:
        if note:
            evidence.append(note)
        return FailureStage(stage, source, validator_stage, build_step, _repairable(stage), evidence)

    if event and event[1] == "completed" and event[0] == "completed":
        return result("none", "validator", None)

    # Without the validator's event, a setup failure is inferred only when the
    # package build never visibly started (a segfault in a test is not setup).
    in_build = started or step is not None or any(k in first for k in _SPECIFIC_ORDER)
    pre_build = validator_stage in _VALIDATOR_SETUP | _VALIDATOR_DEPENDENCIES
    in_setup = pre_build or (not event and not in_build
                             and ("setup" in first or "dependencies" in first))
    if in_setup:
        source = "validator" if pre_build else "log"
        if "dependencies" in first or validator_stage in _VALIDATOR_DEPENDENCIES:
            support("dependencies", "setup")
            return result("dependencies", source, None,
                          "build dependencies could not be resolved or installed")
        support("setup")
        return result("setup", source, None,
                      "failed while preparing the build environment, before the package build ran")

    # Inside the package build: the earliest specific message names the stage.
    specific = [k for k in _SPECIFIC_ORDER if k in first]
    if "dependencies" in first:
        specific.append("dependencies")
    source = "validator" if event else "log"
    if specific:
        kind = min(specific, key=lambda k: first[k][0])
        support(kind, *[k for k in specific if k != kind])
        return result(kind, source, first[kind][2])
    if step in _STEP_STAGE:
        return result(_STEP_STAGE[step], source, step,
                      f"no specific error recognized; inferred from the build step {step} that was running")
    if "setup" in first:
        support("setup")
    return result("unknown", source if event else "none", step)
