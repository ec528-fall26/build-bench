"""Validate and apply literal edits to existing worktree files.

Edits are independent and processed in order. Rejected edits leave the file
unchanged, are logged to stderr through logging, and do not undo earlier edits.
Build-policy checks are conservative heuristics, not semantic verification.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
import fnmatch
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import stat
from typing import TYPE_CHECKING, Iterator
import unicodedata

if TYPE_CHECKING:
    from .types import Edit  # Shared dataclass belongs to Part 1.


LOGGER = logging.getLogger(__name__)
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_POLICY_LINE_CHARS = 64 * 1024
_RESERVED = {".git", ".hg", ".svn", "__pycache__"}
_SKIP_TESTS = re.compile(
    r"\bnocheck\b|--(?:disable-tests?|skip-tests?|nocheck)\b|"
    r"(?<![\w])(?:-D)?(?:BUILD_TESTING|BUILD_TESTS|ENABLE_TESTS|WITH_TESTS|tests?)"
    r"(?::[A-Za-z][\w]*)?\s*=\s*(?:OFF|FALSE|NO|0|disabled)\b|"
    r"\b(?:BUILD_TESTING|BUILD_TESTS|ENABLE_TESTS|WITH_TESTS)\s+(?:OFF|FALSE|0)\b|"
    r"\b(?:SKIP_TESTS|DISABLE_TESTS)\s*[:=]\s*(?:ON|TRUE|1)\b|"
    r"\bDISABLED\s+TRUE\b|\b(?:pytest\.mark\.skip\w*|skipTest)\b",
    re.IGNORECASE,
)
_ARCH_FIELD = re.compile(r"^\s*(Architecture|ExclusiveArch|ExcludeArch)\s*:\s*(.*)$", re.I)
_NEGATED_ARCH = re.compile(r"\[[^\[\]\n]*![^\[\]\n]+\]")
_RPM_SECTION = re.compile(
    r"^%(?:files|prep|build|install|check|clean|changelog|description|package|"
    r"generate_buildrequires|conf|pre|post|preun|postun|pretrans|posttrans|"
    r"preuntrans|postuntrans|verify|trigger(?:in|un|postun|prein)?|"
    r"(?:trans)?filetrigger(?:in|un|postun)?)\b"
)
_MAKE_ALIASES = {"make", "gmake", "$(MAKE)", "${MAKE}", "$MAKE"}
_TEST_TARGETS = {"check", "test", "tests", "test-suite"}


class EditRejected(ValueError):
    """An edit failed validation and has not been written."""


def _active_lines(text: str) -> list[str]:
    return [line.split("#", 1)[0].rstrip() for line in text.splitlines()]


def _is_build_control(path: PurePosixPath) -> bool:
    return (
        path.parts[-2:] == ("debian", "rules")
        or path.name.lower().startswith(("makefile", "gnumakefile"))
        or path.name in {"CMakeLists.txt", "meson.build", "configure.ac", "configure.in", "configure"}
        or path.suffix.lower() in {".spec", ".mk", ".cmake", ".sh"}
    )


def _logical_lines(text: str) -> Iterator[str]:
    # Bound input to the shell lexer, including backslash-continued commands.
    pieces: list[str] = []
    size = 0
    for line in text.splitlines():
        size += len(line)
        if size > MAX_POLICY_LINE_CHARS:
            raise EditRejected("policy_line_too_long")
        continued = line.endswith("\\")
        pieces.append(line[:-1] if continued else line)
        if not continued:
            yield " ".join(pieces)
            pieces, size = [], 0
    if pieces:
        yield " ".join(pieces)


def _shell_commands(line: str) -> Iterator[tuple[str, list[str], bool]]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    separator, words = "", []
    for token in lexer:
        if token and all(char in ";&|" for char in token):
            if words:
                yield separator, words, re.match(r"^[@+-]*-", words[0]) is not None
            separator, words = token, []
        else:
            words.append(token)
    if words:
        yield separator, words, re.match(r"^[@+-]*-", words[0]) is not None


def _command_words(words: list[str]) -> list[str]:
    words = words.copy()
    words[0] = words[0].lstrip("@-+")
    index = 0
    while index < len(words) and (
        words[index] in {"env", "command", "exec", "then", "do"}
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[index])
    ):
        index += 1
    return words[index:]


def _command_kind(words: list[str]) -> tuple[str | None, tuple[str, str] | None]:
    if not words:
        return None, None
    command, args = words[0], words[1:]
    command = command if command in _MAKE_ALIASES else command.rsplit("/", 1)[-1]
    if command in _MAKE_ALIASES or command == "ninja":
        command = "make" if command in _MAKE_ALIASES else "ninja"
        skip_argument = False
        for argument in args:
            if skip_argument:
                skip_argument = False
            elif argument in {"-C", "--directory", "-f", "--file", "-I", "--include-dir", "-W", "-o"}:
                skip_argument = True
            elif argument in _TEST_TARGETS:
                return command, (command, argument)
        return command, None
    if command in {"dh_auto_test", "ctest", "pytest", "tox"}:
        return None, (command, "test")
    if re.fullmatch(r"python[\d.]*", command) and args[:1] == ["-m"] and len(args) > 1:
        if args[1] in {"pytest", "unittest"}:
            return None, (args[1], "test")
    if command in {"meson", "cargo", "go", "npm", "yarn"} and args[:1] == ["test"]:
        return None, (command, "test")
    if command in {"dh_auto_build", "%cmake_build", "%make_build"}:
        return command, None
    if (command, args[:1]) in [("cmake", ["--build"]), ("meson", ["compile"]), ("cargo", ["build"]), ("go", ["build"])]:
        return command, None
    if command == "npm" and args[:2] == ["run", "build"]:
        return command, None
    return None, None


def _command_summary(text: str) -> tuple[Counter, Counter, Counter]:
    tests, builds, masked = Counter(), Counter(), Counter()
    try:
        for line in _logical_lines(text):
            preceding_tests: set[tuple[str, str]] = set()
            for separator, raw, ignored in _shell_commands(line):
                words = _command_words(raw)
                if not words:
                    continue
                build, test = _command_kind(words)
                if build:
                    builds[build] += 1
                if test:
                    tests[test] += 1
                    preceding_tests.add(test)
                    if ignored:
                        masked[(test, "ignore_exit")] += 1
                if words[0] == "set" and ("+e" in words[1:] or words[1:] == ["+o", "errexit"]):
                    masked[("shell", "errexit_disabled")] += 1
                succeeds = words[0] in {"true", ":", "echo", "printf"} or words[:2] in (["exit", "0"], ["return", "0"])
                propagates_failure = words[0] == "false" or (
                    words[0] in {"exit", "return"} and len(words) == 2
                    and (words[1] == "$?" or words[1].isdigit() and int(words[1]) != 0)
                )
                if (separator == ";" and succeeds) or (separator == "||" and not propagates_failure):
                    for previous in preceding_tests:
                        masked[(previous, "success_after_failure")] += 1
    except ValueError as error:
        if isinstance(error, EditRejected):
            raise
        raise EditRejected("unparseable_build_command") from error
    return tests, builds, masked


def _disabled_test_overrides(text: str) -> set[str]:
    disabled: set[str] = set()
    targets: list[str] = []
    recipe: list[str] = []

    def finish() -> None:
        if targets and not _command_summary("\n".join(recipe))[0]:
            disabled.update(targets)

    for line in _logical_lines(text):
        if line.startswith("\t"):
            recipe.append(line)
        elif line.strip() and not line.lstrip().startswith("#"):
            finish()
            targets, recipe = [], []
            header = re.match(r"^([^:=]+):(?!=)(.*)$", line)
            if header:
                targets = [name for name in header[1].split() if re.fullmatch(r"override_dh_auto_test(?:-(?:arch|indep))?", name)]
                if ";" in header[2]:
                    recipe.append(header[2].split(";", 1)[1])
    finish()
    return disabled


def _dependency_arch_exclusions(lines: list[str]) -> Counter:
    values: list[str] = []
    in_dependencies = False
    for line in lines:
        if line and not line[0].isspace():
            field, _, value = line.partition(":")
            in_dependencies = field.lower() in {"build-depends", "build-depends-arch", "build-depends-indep"}
            if in_dependencies:
                values.append(value)
        elif not line.strip():
            in_dependencies = False
        elif in_dependencies:
            values.append(line)
    return Counter(_NEGATED_ARCH.findall("\n".join(values)))


def _rpm_install_lists(lines: list[str]) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        stripped = line.strip()
        if _RPM_SECTION.match(stripped):
            current = stripped if stripped.startswith("%files") else None
            if current is not None:
                sections[current] = []
        elif current is not None and stripped:
            sections[current].append(stripped)
    return {name: "\n".join(entries) for name, entries in sections.items()}


def _policy_reason(path: str, before: str, after: str) -> str | None:
    """Catch explicit build/test bypasses without claiming to prove intent."""
    name = PurePosixPath(path)
    build_control = _is_build_control(name)
    debian_control = name.parts[-2:] == ("debian", "control")
    install_list = "debian" in name.parts and (name.name == "install" or name.suffix == ".install")
    if before.strip() and not after.strip():
        return "file_emptied"
    if not (build_control or debian_control or install_list):
        return None
    if build_control:
        old_tests, old_builds, old_masked = _command_summary(before)
        new_tests, new_builds, new_masked = _command_summary(after)
        if old_tests - new_tests:
            return "test_invocation_removed"
        if old_builds - new_builds:
            return "build_invocation_removed"
        if new_masked - old_masked:
            return "test_failure_masked"
        if name.parts[-2:] == ("debian", "rules"):
            if _disabled_test_overrides(after) - _disabled_test_overrides(before):
                return "test_override_disabled"
    old_lines, new_lines = _active_lines(before), _active_lines(after)
    old_code, new_code = "\n".join(old_lines), "\n".join(new_lines)
    if build_control and Counter(_SKIP_TESTS.findall(new_code.lower())) - Counter(_SKIP_TESTS.findall(old_code.lower())):
        return "test_bypass_added"
    # Protect package architecture declarations, while allowing source-level
    # architecture guards (a legitimate cross-architecture repair).
    old_fields = Counter(
        (match[1].lower(), match[2].strip()) for line in old_lines
        if (match := _ARCH_FIELD.match(line))
    )
    new_fields = Counter(
        (match[1].lower(), match[2].strip()) for line in new_lines
        if (match := _ARCH_FIELD.match(line))
    )
    if (debian_control or name.suffix == ".spec") and any(value for (_, value) in new_fields - old_fields):
        return "architecture_restriction_changed"
    if debian_control and _dependency_arch_exclusions(new_lines) - _dependency_arch_exclusions(old_lines):
        return "architecture_exclusion_added"
    if install_list:
        if old_code.strip() and not new_code.strip():
            return "install_list_emptied"
    if name.suffix == ".spec":
        new_lists = _rpm_install_lists(new_lines)
        if any(entries and not new_lists.get(section) for section, entries in _rpm_install_lists(old_lines).items()):
            return "install_list_emptied"
    return None


def _path_parts(path: str) -> tuple[str, ...]:
    if (
        not path or "\\" in path or "'" in path or '"' in path
        or re.match(r"^[A-Za-z]:", path)
        or any(char.isspace() or unicodedata.category(char).startswith("C") for char in path)
    ):
        raise EditRejected("invalid_path: expected a relative POSIX file path")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} | _RESERVED for part in parts):
        raise EditRejected("invalid_path: traversal, reserved paths and empty components are forbidden")
    return parts


def _check_patch_policy(path: str, allowed_prefix: str, patch_policy: Mapping | None) -> None:
    if not path.startswith(allowed_prefix):
        raise EditRejected("path_outside_allowed_prefix")
    if patch_policy is not None:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patch_policy["forbidden_paths"]):
            raise EditRejected("path_forbidden_by_policy")
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in patch_policy["allowed_paths"]):
            raise EditRejected("path_not_allowed_by_policy")


def _validate_policy(allowed_prefix: str, patch_policy: Mapping | None) -> None:
    if not isinstance(allowed_prefix, str):
        raise ValueError("allowed_prefix must be a string")
    if allowed_prefix:
        if not allowed_prefix.endswith("/"):
            raise ValueError("allowed_prefix must end in '/' or be explicitly empty")
        _path_parts(allowed_prefix[:-1])
    if patch_policy is not None:
        if not isinstance(patch_policy, Mapping):
            raise ValueError("patch_policy must contain allowed_paths and forbidden_paths")
        for field in ("allowed_paths", "forbidden_paths"):
            patterns = patch_policy.get(field)
            if not isinstance(patterns, (list, tuple)) or any(not isinstance(p, str) or not p for p in patterns):
                raise ValueError(f"patch_policy.{field} must be a list of nonempty glob patterns")


@contextmanager
def _parent_directory(root_fd: int, parts: tuple[str, ...]) -> Iterator[int]:
    """Open each directory relative to the root, never following a symlink."""
    parent_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        yield parent_fd
    finally:
        os.close(parent_fd)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(parent_fd: int, filename: str) -> tuple[str, os.stat_result]:
    # O_NONBLOCK prevents a FIFO supplied as an edit target from hanging us.
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise EditRejected("not_regular_file")
        if info.st_size > MAX_FILE_BYTES:
            raise EditRejected("file_too_large")
        raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise EditRejected("file_too_large")
        if _identity(os.fstat(source.fileno())) != _identity(info):
            raise EditRejected("concurrent_file_change")
    if b"\0" in raw:
        raise EditRejected("binary_file: NUL bytes are not supported")
    return raw.decode("utf-8"), info


def _write_file(parent_fd: int, filename: str, content: bytes, original: os.stat_result) -> None:
    """Replace only after a full write, preserving mode and removing scratch files."""
    temporary = f".bb-edit-{secrets.token_hex(12)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(content)
            target.flush()
            os.fchmod(target.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(target.fileno())
        current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(current) != _identity(original):
            raise EditRejected("concurrent_file_change")
        os.replace(temporary, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _apply_one(root_fd: int, edit: Edit, allowed_prefix: str, patch_policy: Mapping | None) -> None:
    if not all(isinstance(getattr(edit, field, None), str) for field in ("path", "old_text", "new_text")):
        raise EditRejected("invalid_edit: path, old_text and new_text must be strings")
    parts = _path_parts(edit.path)
    _check_patch_policy(edit.path, allowed_prefix, patch_policy)
    if not edit.old_text:
        raise EditRejected("empty_old_text")
    if edit.old_text == edit.new_text:
        raise EditRejected("no_change")
    if "\0" in edit.new_text:
        raise EditRejected("binary_replacement")
    # Validate even surrogate-containing strings before any filesystem mutation.
    edit.old_text.encode("utf-8")
    edit.new_text.encode("utf-8")
    with _parent_directory(root_fd, parts) as parent_fd:
        before, info = _read_file(parent_fd, parts[-1])
        first = before.find(edit.old_text)
        if first < 0:
            raise EditRejected("old_text_not_found")
        if before.find(edit.old_text, first + 1) >= 0:
            raise EditRejected("old_text_ambiguous")
        after = before[:first] + edit.new_text + before[first + len(edit.old_text):]
        reason = _policy_reason(edit.path, before, after)
        if reason:
            raise EditRejected(reason)
        encoded = after.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise EditRejected("result_too_large")
        if not before.endswith("\n") or not after.endswith("\n"):
            LOGGER.warning(
                "edit_patch_compatibility_risk path=%r reason=rc2_missing_final_newline; "
                "harness must check canonical patch application", edit.path,
            )
        _write_file(parent_fd, parts[-1], encoded, info)


def apply_edits(
    worktree: Path, edits: list[Edit], *, allowed_prefix: str = "input/",
    patch_policy: Mapping | None = None,
) -> int:
    """Return the number of accepted edit operations, not distinct files.

    Uses Part 1's Edit objects (path, old_text, new_text). No runtime dependency
    on that module is needed. Existing regular UTF-8 files up to 8 MiB only;
    new files, deletes, symlinks and metadata paths are outside this baseline.
    Defaults to the released generator's input/ prefix. The caller supplies
    trusted case policy (allowed_paths/forbidden_paths), separate from proposed edits.
    Explicit allowed_prefix="" permits a source-root research worktree.
    Logs every rejection without logging file contents. Earlier successful
    edits remain when a later edit fails. Invalid roots raise to the agent shell.
    """
    _validate_policy(allowed_prefix, patch_policy)
    root_fd = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    applied = 0
    try:
        for index, edit in enumerate(edits):
            try:
                _apply_one(root_fd, edit, allowed_prefix, patch_policy)
            except (EditRejected, OSError, UnicodeError) as error:
                LOGGER.warning(
                    "edit_rejected index=%d path=%r reason=%s",
                    index, getattr(edit, "path", None), error,
                )
            else:
                applied += 1
                LOGGER.info("edit_applied index=%d path=%r", index, edit.path)
    finally:
        os.close(root_fd)
    return applied
