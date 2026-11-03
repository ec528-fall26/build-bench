"""Bounded snapshots of existing package files before a repair attempt."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat

# Internal dependency on the existing applier's filesystem protections.
from .edit_applier import (
    EditRejected,
    _check_patch_policy,
    _identity,
    _parent_directory,
    _path_parts,
    _read_file,
    _validate_policy,
)

MAX_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_FILES = 256


@dataclass(frozen=True)
class _SavedFile:
    path: str
    content: bytes
    mode: int


def _open_root(worktree: Path) -> int:
    return os.open(
        worktree,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )


def _restore_file(
    parent_fd: int,
    filename: str,
    saved: _SavedFile,
    expected: os.stat_result,
) -> None:
    """Replace one file completely, checking its identity before replacement."""
    temporary = f".bb-restore-{secrets.token_hex(12)}"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(saved.content)
            target.flush()
            os.fchmod(target.fileno(), saved.mode)
            os.fsync(target.fileno())

        current = os.stat(
            filename, dir_fd=parent_fd, follow_symlinks=False
        )
        if _identity(current) != _identity(expected):
            raise EditRejected("concurrent_file_change_during_restore")

        os.replace(
            temporary,
            filename,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class CandidateSnapshot:
    worktree: Path
    files: tuple[_SavedFile, ...]
    root_identity: tuple[int, int]

    @classmethod
    def capture(
        cls,
        worktree: Path,
        paths: Iterable[str],
        *,
        patch_policy: Mapping | None = None,
    ) -> CandidateSnapshot:
        """Capture every requested file, or fail without changing the tree."""
        _validate_policy("input/", patch_policy)
        root = Path(worktree).absolute()
        root_fd = _open_root(root)

        saved = []
        seen = set()
        total_bytes = 0

        try:
            root_info = os.fstat(root_fd)

            for name in paths:
                if not isinstance(name, str):
                    raise ValueError("Snapshot paths must be strings.")

                parts = _path_parts(name)
                _check_patch_policy(name, "input/", patch_policy)

                if name in seen:
                    continue
                if len(saved) >= MAX_FILES:
                    raise ValueError("Snapshot file limit reached.")

                with _parent_directory(root_fd, parts) as parent_fd:
                    text, info = _read_file(parent_fd, parts[-1])

                # UTF-8 decoding/encoding preserves the supported bytes,
                # including CRLF and a missing final newline.
                content = text.encode("utf-8")
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("Snapshot file exceeds size limit.")
                if total_bytes + len(content) > MAX_SNAPSHOT_BYTES:
                    raise ValueError("Snapshot total size limit reached.")

                saved.append(
                    _SavedFile(
                        path=name,
                        content=content,
                        mode=stat.S_IMODE(info.st_mode),
                    )
                )
                seen.add(name)
                total_bytes += len(content)

            return cls(
                worktree=root,
                files=tuple(saved),
                root_identity=(root_info.st_dev, root_info.st_ino),
            )
        finally:
            os.close(root_fd)

    def restore(self) -> None:
        """Restore captured files; failures propagate to the caller.

        Each replacement is atomic, but the entire multi-file restoration
        is not a transaction. A failure must not be reported as success.
        """
        root_fd = _open_root(self.worktree)
        try:
            root_info = os.fstat(root_fd)
            if (root_info.st_dev, root_info.st_ino) != self.root_identity:
                raise EditRejected("worktree_replaced_since_capture")

            # Check all targets before writing any of them.
            expected = {}
            for saved in self.files:
                parts = _path_parts(saved.path)
                with _parent_directory(root_fd, parts) as parent_fd:
                    _, info = _read_file(parent_fd, parts[-1])
                expected[saved.path] = info

            for saved in self.files:
                parts = _path_parts(saved.path)
                with _parent_directory(root_fd, parts) as parent_fd:
                    _restore_file(
                        parent_fd,
                        parts[-1],
                        saved,
                        expected[saved.path],
                    )
        finally:
            os.close(root_fd)
