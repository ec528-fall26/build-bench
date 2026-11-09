"""Facts about the frozen dependency set that a build log cannot show (M3).

Two of the misses of 8 November were decided by what a dependency package
installs, not by the source: ectrans (a CMake export naming a Fortran module
directory that no package ships) and pybigwig (a runtime library package that
ships no library, so `libBigWig.so` in the -dev package dangles). Both are
visible in the case's .deb files (`deb_archive`), which the model otherwise
has to think of opening with `deb_contents`.

This module reads those files once, bounded (about 30 s, then it stops and
says "partial"), and reports only three kinds of fact:

  * a .so symlink in a -dev package whose target no package in the set ships;
  * a runtime library package (libfoo1, libfoo0t64) that ships no library;
  * an absolute path named in a *-targets.cmake or *.pc file that no package
    ships, with the nearest directory that does exist.

A relevance gate keeps the block quiet: a finding is shown only when its
package or path tokens occur in the first fatal region of the log or in
Build-Depends. On cases where the dependencies are not the problem (rcran,
libunwind, libbpp-core) the result is the empty string.

Read-only. `render` never raises: any failure returns "".
"""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path, PurePosixPath
import re
import time

from . import archnames, deb_archive

log = logging.getLogger(__name__)

BUDGET_SECONDS = 30.0
MAX_LINES = 8
MAX_TEXT_FILES = 12
REGION_BEFORE, REGION_AFTER, LINE_CHARS = 3, 40, 300
_MIN_KEY = 4          # shorter tokens ("z", "c") match everything

# A failure line, not a package name that happens to contain "error" (libgpg-error0).
_FATAL = re.compile(
    r"(?i)(?<![\w-])error(?![\w-])\s*[:=]|fatal error|cmake error|undefined reference|"
    r"no such file or directory|cannot find -l|ld returned|relocation r_|recompile with -fpic|"
    r"\*\*\* \[|^E: ")
_NOISE = re.compile(r"^(Get:|Unpacking|Preparing|Setting up|Selecting|Package versions:|Reading |"
                    r"Fetched|Hit:|Building dependency|The following|Merged Build|Filtered Build|"
                    r"Build-Depends)")
_NOT_RUNTIME = ("-dev", "-doc", "-dbg", "-data", "-common", "-bin", "-utils", "-tools", "-perl",
                "-java", "-l10n", "-locale", "-plugins", "-dbgsym")
_RUNTIME_NAME = re.compile(r"^lib[a-z0-9+.-]*\d(t64|d|v5|ldbl|g)?$")
_ABS_PATH = re.compile(r"/(?:usr|lib|lib64|opt)/[^\s\"';:)<>,${}*|\\]+")
_EXPORT_FILES = ("-targets.cmake", "-targets-release.cmake", "-targets-noconfig.cmake", ".pc")


def fatal_region(log_lines: list[str]) -> str:
    """Text of the first fatal region: a few lines before the first failure line and the lines after it."""
    lines = [str(line)[:LINE_CHARS] for line in log_lines]
    for index, line in enumerate(lines):
        if _FATAL.search(line) and not _NOISE.match(line):
            return "\n".join(lines[max(0, index - REGION_BEFORE):index + REGION_AFTER])
    return "\n".join(lines[-60:])


def build_depends(worktree: Path, log_lines: list[str]) -> set[str]:
    """Package names in Build-Depends: from debian/control, or the log's Merged Build-Depends line."""
    text = ""
    try:
        control = Path(worktree) / "input" / "debian" / "control"
        if control.is_file() and control.stat().st_size < 1_000_000:
            body = control.read_text(encoding="utf-8", errors="replace")
            text = " ".join(m.group(1) for m in re.finditer(
                r"(?mi)^Build-Depends(?:-Arch|-Indep)?:[ \t]*((?:.*)(?:\n[ \t].*)*)", body))
    except OSError:
        pass
    if not text:
        for line in log_lines:
            if line.startswith(("Merged Build-Depends:", "Build-Depends:")):
                text += " " + line.split(":", 1)[1]
    names = set()
    for item in re.split(r"[,|]", text):
        found = re.match(r"\s*([a-z0-9][a-z0-9+.-]+)", item.lower())
        if found:
            names.add(found.group(1))
    return names


def _stem(package: str) -> str:
    """libbigwig0t64 -> bigwig, libfiat-dev -> fiat: the part a log or Build-Depends would also say."""
    name = package.lower()
    name = name[3:] if name.startswith("lib") else name
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r"(-dev|-bin|-common|-data|t64|ldbl|v5|[0-9][0-9.+-]*|d)$", "", name)
    return name if len(name) >= _MIN_KEY else ""


class _Gate:
    """Does a package or path have anything to do with this failure?"""

    def __init__(self, region: str, depends: set[str]):
        self.region = region.lower()
        self.depends = depends
        self.depends_text = " ".join(sorted(depends))

    def package(self, name: str) -> bool:
        name = name.lower()
        if name in self.depends:
            return True
        stem = _stem(name)
        return bool(stem) and (stem in self.region or stem in self.depends_text)

    def path(self, path: str) -> bool:
        return path.lower() in self.region

    def leaf(self, path: str) -> bool:
        name = PurePosixPath(path).name.lower()
        return len(name) >= _MIN_KEY and name in self.region


def _dep_debs(worktree: Path, target_arch: str | None) -> list[Path]:
    arch = archnames.debian(target_arch)
    dirs = deb_archive.dependency_dirs(worktree)
    chosen = [d for d in dirs if arch and d.name.endswith("-" + arch)] or dirs
    return [deb for d in chosen for deb in sorted(d.glob("*.deb"))]


def _package_name(deb: Path) -> str:
    return deb.name.split("_", 1)[0]


def _order(debs: list[Path], gate: _Gate) -> list[Path]:
    """Relevant packages first, then small before large, so a time stop leaves the useful part read."""
    def key(deb: Path):
        try:
            size = deb.stat().st_size
        except OSError:
            size = 0
        return (0 if gate.package(_package_name(deb)) else 1, size)
    return sorted(debs, key=key)


def _resolve(directory: str, target: str) -> str:
    joined = target if target.startswith("/") else f"{directory}/{target}"
    return os.path.normpath("/" + joined.lstrip("/"))


def _nearest_directory(missing: str, listings: dict[str, list]) -> str:
    """The shipped directory most like a missing one: same last component, closest path."""
    leaf = PurePosixPath(missing).name
    if not leaf:
        return ""
    best, best_score, needle = "", 0.0, f"/{leaf}/"
    for package, members in listings.items():
        for path, _kind, _extra in members:
            full = "/" + path
            at = (full + "/").find(needle)
            if at < 0:
                continue
            candidate = full[:at + len(needle) - 1]
            score = difflib.SequenceMatcher(None, candidate, missing).ratio()
            if score > best_score:
                best, best_score = f"{candidate} (shipped by {package})", score
    return best


def audit(worktree: Path, log_lines: list[str], target_arch: str | None, *,
          budget: float = BUDGET_SECONDS, clock=time.monotonic) -> list[str]:
    """Relevant dependency facts as short sentences; the last may say the read was partial."""
    started = clock()
    region = fatal_region(log_lines)
    gate = _Gate(region, build_depends(worktree, log_lines))
    debs = _order(_dep_debs(worktree, target_arch), gate)
    if not debs:
        return []

    cache: dict = {}
    listings: dict[str, list] = {}     # package -> members
    unread = []
    for position, deb in enumerate(debs):
        if clock() - started > budget:
            unread.extend(_package_name(d) for d in debs[position:])
            break
        listing = deb_archive.members(deb, cache)
        if listing is None or listing.undecodable:
            unread.append(_package_name(deb))
        else:
            listings[_package_name(deb)] = listing.members
    partial = bool(unread)

    shipped: dict[str, str] = {}        # path -> package, for files and links
    for package, members in listings.items():
        for path, _kind, _extra in members:
            shipped["/" + path] = package
    prefixes: set[str] = set()
    for path in shipped:
        parent = path.rsplit("/", 1)[0]
        while parent and parent not in prefixes:
            prefixes.add(parent)
            parent = parent.rsplit("/", 1)[0]

    def exists(path: str) -> bool:
        path = path.rstrip("/")
        return path in shipped or path in prefixes

    facts: list[str] = []

    # 1. dangling .so links in -dev packages
    empty_runtime = {p for p, members in listings.items()
                     if _RUNTIME_NAME.match(p) and not p.endswith(_NOT_RUNTIME)
                     and not any(".so" in path for path, _k, _e in members)}
    named_empty: set[str] = set()
    for package, members in listings.items():
        if not package.endswith("-dev"):
            continue
        for path, kind, target in members:
            if kind != "l" or not (path.endswith(".so") or ".so." in path):
                continue
            resolved = _resolve("/" + path.rsplit("/", 1)[0], target)
            if exists(resolved):
                continue
            library = PurePosixPath(path).name
            stem = re.sub(r"^lib|\.so.*$", "", library.lower())
            runtime = sorted(p for p in empty_runtime if stem and p.startswith("lib" + stem))
            if not (gate.package(package) or gate.package(library) or any(gate.package(p) for p in runtime)):
                continue
            line = (f"{package} ships {path} -> {target}, but no package in the set ships "
                    f"{PurePosixPath(resolved).name}")
            if runtime:
                named_empty.update(runtime)
                line += f"; the runtime package {', '.join(runtime)} ships no library file"
            facts.append(line + (" (the set may be incomplete)" if partial else ""))

    # 2. runtime library packages that ship no library
    # (Only a package with no files at all: one that ships docs may install its library through
    # dpkg alternatives, as libopenblas0 does, and the list would call it empty by mistake.)
    for package in sorted(empty_runtime - named_empty):
        if gate.package(package) and not listings[package]:
            facts.append(f"{package} is a runtime library package and ships no files at all")

    # 3. absolute paths in *-targets.cmake and *.pc that nothing ships
    wanted: dict[str, set[str]] = {}
    for package, members in listings.items():
        if not gate.package(package):
            continue
        for path, kind, _extra in members:
            if kind == "f" and path.endswith(_EXPORT_FILES):
                wanted.setdefault(package, set()).add(path)
    debs_by_name = {_package_name(d): d for d in debs}
    read = 0
    for package, paths in sorted(wanted.items()):
        if clock() - started > budget or read >= MAX_TEXT_FILES:
            partial = True
            break
        picked = set(sorted(paths)[:MAX_TEXT_FILES - read])
        read += len(picked)
        texts = deb_archive.read_texts(debs_by_name[package], picked)
        missing: dict[str, str] = {}      # named path -> export file that names it
        for path, text in sorted(texts.items()):
            for found in _ABS_PATH.finditer(text):
                if not _path_start_ok(text, found.start()):
                    continue
                named = found.group(0).rstrip("/.")
                if named not in missing and not exists(named):
                    missing[named] = path
        # Show a missing path when the log names it (or its last component), and its siblings: ectrans'
        # log names fiat, while parkind_sp and parkind_dp beside it fail the same way.
        hits = {n for n in missing if gate.path(n) or gate.leaf(n)}
        parents = {n.rsplit("/", 1)[0] for n in hits}
        for named, path in missing.items():
            if named not in hits and named.rsplit("/", 1)[0] not in parents:
                continue
            line = (f"{PurePosixPath(path).name} (in {package}) names {named}, which no package in the "
                    "set ships")
            near = _nearest_directory(named, listings)
            if near:
                line += f"; nearest existing directory: {near}"
            if partial:
                line += " (the set may be incomplete)"
            facts.append(line)

    facts = list(dict.fromkeys(facts))
    if facts and partial:
        facts.append(f"partial: {len(listings)} of {len(debs)} packages were read in time; "
                     "absence claims above may be wrong for the others")
    return facts


def _path_start_ok(text: str, start: int) -> bool:
    """A real path starts after whitespace, a quote, '=', '-I', '-L' or similar, not inside a word."""
    if start == 0:
        return True
    before = text[start - 1]
    if before in "IL" and start >= 2 and text[start - 2] == "-":
        return True
    return not (before.isalnum() or before in "_.}/$")


def render(worktree: Path, log_lines: list[str], target_arch: str | None) -> str:
    """The DEPENDENCY FACTS block for the opening message, or "" when nothing is relevant."""
    try:
        facts = audit(Path(worktree), list(log_lines or []), target_arch)
    except Exception as error:  # noqa: BLE001 - an audit must never stop a repair
        log.warning("dependency audit failed: %s: %s", type(error).__name__, error)
        return ""
    if not facts:
        return ""
    lines = ["DEPENDENCY FACTS (read from the case's frozen .deb files; nothing was installed):"]
    lines.extend(f"- {fact}" for fact in facts[:MAX_LINES])
    return "\n".join(lines)
