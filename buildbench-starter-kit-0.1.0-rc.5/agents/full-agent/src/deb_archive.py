"""Read the dependency packages a case ships, without installing anything.

The kit's runner copies the whole case directory into the agent's worktree
(`runner/run-agent-case.sh`), so `dependencies/<dist>-<arch>/*.deb` and
`config/dependency-lock.json` sit next to `input/`. Two of the four misses of
8 November (ectrans, pybigwig) were decided by what a dependency installed:
a module directory that was not where its CMake export said, and a runtime
library package that shipped no library. Both facts are in these files.

A .deb is an `ar` archive: `debian-binary`, `control.tar.*`, `data.tar.*`.
The `ar` layer and gzip/xz are standard library; Ubuntu compresses with zstd,
which needs Python 3.14's `compression.zstd`, the `zstandard` wheel, or a
`zstd` binary. Without any of them the member sizes are still reported, and a
`data.tar.zst` of a few hundred bytes is an empty package.

Read-only, bounded, never raises out of its public functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

MAX_DEB_BYTES = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_ENTRIES = 400
_DEP_DIRS = ("dependencies",)
_AR_MAGIC = b"!<arch>\n"


@dataclass
class DebInfo:
    path: Path
    package: str
    version: str
    arch: str
    size: int                      # bytes of the .deb
    data_member: str = ""          # data.tar.zst / .xz / .gz
    data_size: int = 0             # compressed bytes of the data member
    entries: list[str] = field(default_factory=list)   # "path", "path -> target", "path (N bytes)"
    note: str = ""                 # why entries are empty, if they are


def dependency_dirs(worktree: Path) -> list[Path]:
    """The directories holding the case's frozen .deb files, if present."""
    out = []
    for name in _DEP_DIRS:
        root = Path(worktree) / name
        if root.is_dir():
            out.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    return out


def find_debs(worktree: Path, package: str) -> list[Path]:
    """The .deb files whose package name equals, or starts with, ``package``."""
    package = package.strip().lower()
    exact, prefix = [], []
    for directory in dependency_dirs(worktree):
        for deb in sorted(directory.glob("*.deb")):
            name = deb.name.split("_", 1)[0].lower()
            if name == package:
                exact.append(deb)
            elif name.startswith(package) or package in name:
                prefix.append(deb)
    return exact or prefix


def lock_entries(worktree: Path, package: str) -> list[dict]:
    """Entries of config/dependency-lock.json matching ``package`` (name, version, arch)."""
    lock = Path(worktree) / "config" / "dependency-lock.json"
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    package = package.strip().lower()
    return [d for d in data.get("dependencies", []) if isinstance(d, dict)
            and package in str(d.get("package", "")).lower()]


# --- ar ------------------------------------------------------------------------

def ar_members(data: bytes) -> list[tuple[str, int, int]]:
    """(name, offset, size) of each member of an ar archive."""
    if not data.startswith(_AR_MAGIC):
        raise ValueError("not an ar archive")
    members, pos = [], len(_AR_MAGIC)
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        if header[58:60] != b"`\n":
            raise ValueError("corrupt ar header")
        name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip() or "0")
        members.append((name, pos + 60, size))
        pos += 60 + size + (size % 2)
    return members


def _decompress(name: str, blob: bytes) -> bytes | None:
    """The tar bytes of a data member, or None when no decompressor exists."""
    if name.endswith(".gz"):
        import gzip
        return gzip.decompress(blob)
    if name.endswith(".xz") or name.endswith(".lzma"):
        import lzma
        return lzma.decompress(blob)
    if name.endswith(".bz2"):
        import bz2
        return bz2.decompress(blob)
    if name.endswith(".zst"):
        try:
            from compression import zstd  # Python 3.14
            return zstd.decompress(blob)
        except ImportError:
            pass
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
        except ImportError:
            pass
        binary = shutil.which("zstd") or shutil.which("unzstd")
        if binary:
            done = subprocess.run([binary, "-d", "-c", "-q"], input=blob, capture_output=True, timeout=30)
            if done.returncode == 0:
                return done.stdout
        return None
    if name.endswith(".tar"):
        return blob
    return None


def _control_fields(control_tar: bytes | None) -> dict[str, str]:
    if control_tar is None:
        return {}
    try:
        with tarfile.open(fileobj=io.BytesIO(control_tar)) as tar:
            member = next((m for m in tar.getmembers() if PurePosixPath(m.name).name == "control"), None)
            if member is None:
                return {}
            text = tar.extractfile(member).read().decode("utf-8", "replace")
    except (tarfile.TarError, OSError, ValueError):
        return {}
    fields = {}
    for line in text.splitlines():
        if line[:1].isalpha() and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def inspect(deb: Path, *, pattern: str = "") -> DebInfo:
    """Package facts and the file list of one .deb; entries filtered by ``pattern`` (substring)."""
    name_parts = deb.name[:-4].split("_")
    info = DebInfo(path=deb, package=name_parts[0], version=name_parts[1] if len(name_parts) > 1 else "",
                   arch=name_parts[2] if len(name_parts) > 2 else "", size=0)
    try:
        info.size = deb.stat().st_size
        if info.size > MAX_DEB_BYTES:
            info.note = f"package is {info.size:,} bytes, above the {MAX_DEB_BYTES:,}-byte read limit"
            return info
        data = deb.read_bytes()
        members = ar_members(data)
    except (OSError, ValueError) as error:
        info.note = f"cannot read: {error}"
        return info
    data_member = next(((n, o, s) for n, o, s in members if n.startswith("data.tar")), None)
    control_member = next(((n, o, s) for n, o, s in members if n.startswith("control.tar")), None)
    if data_member is None:
        info.note = "no data.tar member"
        return info
    info.data_member, offset, info.data_size = data_member
    if control_member:
        fields = _control_fields(_decompress(control_member[0], data[control_member[1]:control_member[1] + control_member[2]]))
        info.package = fields.get("Package", info.package)
        info.version = fields.get("Version", info.version)
        info.arch = fields.get("Architecture", info.arch)
    if info.data_size > MAX_MEMBER_BYTES:
        info.note = f"{info.data_member} is {info.data_size:,} bytes, above the read limit"
        return info
    try:
        tar_bytes = _decompress(info.data_member, data[offset:offset + info.data_size])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        info.note = f"cannot decompress {info.data_member}: {error}"
        return info
    if tar_bytes is None:
        info.note = (f"{info.data_member} is zstd-compressed and no decompressor is available here "
                     f"(Python 3.14, the zstandard module or a zstd binary); its compressed size is "
                     f"{info.data_size:,} bytes" + (", which is an empty package" if info.data_size < 400 else ""))
        return info
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                path = member.name[2:] if member.name.startswith("./") else member.name
                if pattern and pattern not in path:
                    continue
                if member.issym() or member.islnk():
                    info.entries.append(f"{path} -> {member.linkname}")
                else:
                    info.entries.append(f"{path} ({member.size:,} bytes)")
                if len(info.entries) >= MAX_ENTRIES:
                    info.note = f"list cut at {MAX_ENTRIES} entries; narrow with a filter"
                    break
    except (tarfile.TarError, OSError, ValueError) as error:
        info.note = f"cannot list {info.data_member}: {error}"
    if not info.entries and not info.note:
        info.note = "ships no files" + (f" matching {pattern!r}" if pattern else "")
    return info


def read_text(deb: Path, path: str) -> tuple[str | None, str]:
    """(text, note) of one text file inside the .deb's data member."""
    path = path.lstrip("/")
    try:
        data = deb.read_bytes()
        members = ar_members(data)
    except (OSError, ValueError) as error:
        return None, f"cannot read: {error}"
    data_member = next(((n, o, s) for n, o, s in members if n.startswith("data.tar")), None)
    if data_member is None:
        return None, "no data.tar member"
    name, offset, size = data_member
    try:
        tar_bytes = _decompress(name, data[offset:offset + size])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, f"cannot decompress {name}: {error}"
    if tar_bytes is None:
        return None, f"{name} is zstd-compressed and no decompressor is available here"
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            for member in tar.getmembers():
                candidate = member.name[2:] if member.name.startswith("./") else member.name
                if candidate == path:
                    if not member.isfile():
                        return None, f"{path} is not a regular file in the package"
                    if member.size > MAX_TEXT_BYTES:
                        return None, f"{path} is {member.size:,} bytes, above the {MAX_TEXT_BYTES:,}-byte read limit"
                    blob = tar.extractfile(member).read()
                    try:
                        return blob.decode("utf-8"), ""
                    except UnicodeDecodeError:
                        return None, f"{path} is not UTF-8 text ({member.size:,} bytes)"
    except (tarfile.TarError, OSError, ValueError) as error:
        return None, f"cannot list {name}: {error}"
    return None, f"{path} is not in the package"


def render(info: DebInfo) -> str:
    head = f"{info.package} {info.version} ({info.arch}), {info.size:,} bytes, {info.data_member} {info.data_size:,} bytes"
    lines = [head]
    lines.extend("  " + entry for entry in info.entries)
    if info.note:
        lines.append(f"  [{info.note}]")
    return "\n".join(lines)


_ARCHIVE_NAME = re.compile(r"lib(?P<name>[\w+.-]+?)(?:\.so(?:\.\d+)*|\.a)$")


def library_packages(worktree: Path, library: str) -> list[Path]:
    """The .debs whose name mentions a library (libBigWig -> libbigwig0t64, libbigwig-dev)."""
    base = library.lower()
    base = base[3:] if base.startswith("lib") else base
    return [deb for directory in dependency_dirs(worktree) for deb in sorted(directory.glob("*.deb"))
            if base in deb.name.split("_", 1)[0].lower()]
