"""Read the dependency packages a case ships, without installing anything.

The kit's runner copies the whole case directory into the agent's worktree
(`runner/run-agent-case.sh`), so `dependencies/<dist>-<arch>/*.deb` and
`config/dependency-lock.json` sit next to `input/`. Two of the four misses of
8 November (ectrans, pybigwig) were decided by what a dependency installed:
a module directory that was not where its CMake export said, and a runtime
library package that shipped no library. Both facts are in these files.

A .deb is an `ar` archive: `debian-binary`, `control.tar.*`, `data.tar.*`.
The `ar` layer and gzip/xz are standard library; Ubuntu compresses with zstd,
which needs the pinned `zstandard` wheel (imported lazily), Python 3.14's
`compression.zstd`, or a `zstd` binary. Members are inflated as a stream through
a hard output cap, so a hostile or huge package costs time, not memory. Without any of them the member sizes are still reported, and a
`data.tar.zst` of a few hundred bytes is an empty package.

Read-only, bounded, never raises out of its public functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import itertools
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

MAX_DEB_BYTES = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024   # hard cap on tar bytes read out of one data member
MAX_CONTROL_BYTES = 8 * 1024 * 1024
MAX_LISTED_MEMBERS = 100_000
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


class DecompressedTooLarge(ValueError):
    """A data member inflated past MAX_DECOMPRESSED_BYTES (a decompression bomb or a huge package)."""


class _CappedReader:
    """Wrap a decompressing stream so that no more than ``cap`` bytes ever come out of it."""

    def __init__(self, raw, cap: int):
        self.raw, self.cap, self.total = raw, cap, 0

    def read(self, size: int = -1) -> bytes:
        room = self.cap + 1 - self.total
        size = room if size is None or size < 0 else min(size, room)
        data = self.raw.read(size)
        self.total += len(data)
        if self.total > self.cap:
            raise DecompressedTooLarge(f"data inflates past the {self.cap:,}-byte read limit")
        return data

    def close(self) -> None:
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001 - closing a broken stream must not mask the real error
            pass


def _zstandard():
    """The optional zstandard module, or None. Any import failure counts as absent."""
    try:
        import zstandard
        return zstandard
    except Exception:  # noqa: BLE001 - a missing or broken wheel degrades to the other readers
        return None


def _open_stream(name: str, blob: bytes):
    """A decompressing file object over a data or control member, or None when no reader exists.

    Streaming keeps memory flat: the tar is walked as it inflates, never held whole.
    """
    if name.endswith(".gz"):
        import gzip
        return gzip.GzipFile(fileobj=io.BytesIO(blob))
    if name.endswith(".xz") or name.endswith(".lzma"):
        import lzma
        return lzma.LZMAFile(io.BytesIO(blob))
    if name.endswith(".bz2"):
        import bz2
        return bz2.BZ2File(io.BytesIO(blob))
    if name.endswith(".zst"):
        try:
            from compression import zstd  # Python 3.14
            return zstd.ZstdFile(io.BytesIO(blob))
        except ImportError:
            pass
        module = _zstandard()
        if module is not None:
            # Debs may hold several frames; read_across_frames keeps going past the first.
            return module.ZstdDecompressor().stream_reader(io.BytesIO(blob), read_across_frames=True)
        binary = shutil.which("zstd") or shutil.which("unzstd")
        if binary:
            done = subprocess.run([binary, "-d", "-c", "-q"], input=blob, capture_output=True, timeout=30)
            if done.returncode == 0:
                return io.BytesIO(done.stdout)
        return None
    if name.endswith(".tar"):
        return io.BytesIO(blob)
    return None


def _decompress(name: str, blob: bytes, cap: int = MAX_CONTROL_BYTES) -> bytes | None:
    """All bytes of a small member (the control tar), or None when no decompressor exists."""
    stream = _open_stream(name, blob)
    if stream is None:
        return None
    reader = _CappedReader(stream, cap)
    try:
        chunks = []
        while True:
            chunk = reader.read(1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        reader.close()


def _control_fields(control_tar: bytes | None) -> dict[str, str]:
    if control_tar is None:
        return {}
    try:
        with tarfile.open(fileobj=io.BytesIO(control_tar)) as tar:
            member = next((m for m in tar.getmembers() if PurePosixPath(m.name).name == "control"), None)
            if member is None:
                return {}
            text = tar.extractfile(member).read().decode("utf-8", "replace")
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001 - a damaged control member only loses the name and version
        return {}
    fields = {}
    for line in text.splitlines():
        if line[:1].isalpha() and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


@dataclass
class _Listing:
    """One .deb's whole member list, kept for the length of a call (see ``inspect``'s cache)."""
    package: str
    version: str
    arch: str
    size: int
    data_member: str
    data_size: int
    members: list = field(default_factory=list)   # (path, "l" link | "f" file, target | size)
    note: str = ""
    undecodable: bool = False


def _clean(name: str) -> str:
    return name[2:] if name.startswith("./") else name


def _walk(data: bytes, offset: int, size: int, name: str, state: dict):
    """Yield (path, kind, extra) for each non-directory member, inflating as the tar is read.

    A failure ends the walk and lands in ``state['note']``; the members already yielded stay valid.
    """
    reader = None
    try:
        stream = _open_stream(name, data[offset:offset + size])
        if stream is None:
            state["undecodable"] = True
            return
        reader = _CappedReader(stream, MAX_DECOMPRESSED_BYTES)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if member.isdir():
                    continue
                path = _clean(member.name)
                if member.issym() or member.islnk():
                    yield path, "l", member.linkname
                else:
                    yield path, "f", member.size
    except TimeoutError:
        raise
    except DecompressedTooLarge as error:
        state["note"] = f"listing stopped: {error}"
    except Exception as error:  # noqa: BLE001 - lzma.LZMAError, zlib.error, EOFError, zstandard errors, TarError
        state["note"] = f"cannot list {name}: {type(error).__name__}: {error}"
    finally:
        if reader is not None:
            reader.close()


def _read_header(deb: Path, info: DebInfo):
    """Fill ``info`` from the ar layer and control member; (data, offset, size) of data.tar, or None."""
    try:
        info.size = deb.stat().st_size
        if info.size > MAX_DEB_BYTES:
            info.note = f"package is {info.size:,} bytes, above the {MAX_DEB_BYTES:,}-byte read limit"
            return None
        data = deb.read_bytes()
        members = ar_members(data)
    except TimeoutError:
        raise
    except (OSError, ValueError) as error:
        info.note = f"cannot read: {error}"
        return None
    data_member = next(((n, o, s) for n, o, s in members if n.startswith("data.tar")), None)
    control_member = next(((n, o, s) for n, o, s in members if n.startswith("control.tar")), None)
    if data_member is None:
        info.note = "no data.tar member"
        return None
    info.data_member, offset, info.data_size = data_member
    if control_member:
        try:
            fields = _control_fields(_decompress(control_member[0],
                                                 data[control_member[1]:control_member[1] + control_member[2]]))
        except TimeoutError:
            raise
        except Exception:  # noqa: BLE001
            fields = {}
        info.package = fields.get("Package", info.package)
        info.version = fields.get("Version", info.version)
        info.arch = fields.get("Architecture", info.arch)
    if info.data_size > MAX_MEMBER_BYTES:
        info.note = f"{info.data_member} is {info.data_size:,} bytes, above the read limit"
        return None
    return data, offset, info.data_size


def _no_decompressor_note(info: DebInfo) -> str:
    return (f"{info.data_member} is zstd-compressed and no decompressor is available here "
            f"(Python 3.14, the zstandard module or a zstd binary); its compressed size is "
            f"{info.data_size:,} bytes" + (", which is an empty package" if info.data_size < 400 else ""))


def inspect(deb: Path, *, pattern: str = "", cache: dict | None = None) -> DebInfo:
    """Package facts and the file list of one .deb; entries filtered by ``pattern`` (substring).

    ``cache`` (a dict the caller owns, keyed by deb path) keeps the whole member list so a
    caller that asks about many packages, or the same one twice, inflates each deb once. Without
    it the walk stops as soon as MAX_ENTRIES matches are found.
    """
    name_parts = deb.name[:-4].split("_")
    info = DebInfo(path=deb, package=name_parts[0], version=name_parts[1] if len(name_parts) > 1 else "",
                   arch=name_parts[2] if len(name_parts) > 2 else "", size=0)
    try:
        listing = cache.get(str(deb)) if cache is not None else None
        if listing is None:
            header = _read_header(deb, info)
            if header is None:
                return info
            data, offset, size = header
            state: dict = {}
            walker = _walk(data, offset, size, info.data_member, state)
            if cache is not None:
                members = list(itertools.islice(walker, MAX_LISTED_MEMBERS + 1))
                if len(members) > MAX_LISTED_MEMBERS:
                    del members[MAX_LISTED_MEMBERS:]
                    state.setdefault("note", f"member list cut at {MAX_LISTED_MEMBERS} files")
                walker.close()
                listing = _Listing(info.package, info.version, info.arch, info.size, info.data_member,
                                   info.data_size, members, state.get("note", ""),
                                   bool(state.get("undecodable")))
                cache[str(deb)] = listing
                source = members
            else:
                source = walker
        else:
            info.package, info.version, info.arch = listing.package, listing.version, listing.arch
            info.size, info.data_member, info.data_size = listing.size, listing.data_member, listing.data_size
            source = listing.members
            state = {"note": listing.note, "undecodable": listing.undecodable}
        for path, kind, extra in source:
            if pattern and pattern not in path:
                continue
            info.entries.append(f"{path} -> {extra}" if kind == "l" else f"{path} ({extra:,} bytes)")
            if len(info.entries) >= MAX_ENTRIES:
                info.note = f"list cut at {MAX_ENTRIES} entries; narrow with a filter"
                break
        if cache is None and hasattr(source, "close"):
            source.close()
        if state.get("undecodable"):
            info.note = _no_decompressor_note(info)
        elif state.get("note"):
            info.note = (info.note + "; " if info.note else "") + state["note"]
    except TimeoutError:
        raise
    except Exception as error:  # noqa: BLE001 - the public function never raises
        info.note = f"cannot list {info.data_member or deb.name}: {type(error).__name__}: {error}"
    if not info.entries and not info.note:
        info.note = "ships no files" + (f" matching {pattern!r}" if pattern else "")
    return info


def read_text(deb: Path, path: str) -> tuple[str | None, str]:
    """(text, note) of one text file inside the .deb's data member."""
    path = path.lstrip("/")
    info = DebInfo(path=deb, package="", version="", arch="", size=0)
    reader = None
    try:
        header = _read_header(deb, info)
        if header is None:
            return None, info.note
        data, offset, size = header
        name = info.data_member
        stream = _open_stream(name, data[offset:offset + size])
        if stream is None:
            return None, f"{name} is zstd-compressed and no decompressor is available here"
        reader = _CappedReader(stream, MAX_DECOMPRESSED_BYTES)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if _clean(member.name) != path:
                    continue
                if not member.isfile():
                    return None, f"{path} is not a regular file in the package"
                if member.size > MAX_TEXT_BYTES:
                    return None, f"{path} is {member.size:,} bytes, above the {MAX_TEXT_BYTES:,}-byte read limit"
                blob = tar.extractfile(member).read()
                try:
                    return blob.decode("utf-8"), ""
                except UnicodeDecodeError:
                    return None, f"{path} is not UTF-8 text ({member.size:,} bytes)"
    except TimeoutError:
        raise
    except DecompressedTooLarge as error:
        return None, f"cannot read {info.data_member}: {error}"
    except Exception as error:  # noqa: BLE001 - lzma.LZMAError, zlib.error, EOFError, zstandard errors, TarError
        return None, f"cannot list {info.data_member}: {type(error).__name__}: {error}"
    finally:
        if reader is not None:
            reader.close()
    return None, f"{path} is not in the package"


def members(deb: Path, cache: dict) -> "_Listing | None":
    """The whole member list of ``deb`` (inflating it once into ``cache``), or None when unreadable."""
    inspect(deb, cache=cache)
    return cache.get(str(deb))


def read_texts(deb: Path, paths: set[str], *, max_bytes: int = MAX_TEXT_BYTES) -> dict[str, str]:
    """Text of several small files of one .deb in a single pass over its tar; unreadable ones are left out."""
    wanted = {p.lstrip("/") for p in paths}
    found: dict[str, str] = {}
    reader = None
    try:
        info = DebInfo(path=deb, package="", version="", arch="", size=0)
        header = _read_header(deb, info)
        if header is None or not wanted:
            return found
        data, offset, size = header
        stream = _open_stream(info.data_member, data[offset:offset + size])
        if stream is None:
            return found
        reader = _CappedReader(stream, MAX_DECOMPRESSED_BYTES)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                path = _clean(member.name)
                if path in wanted and member.isfile() and member.size <= max_bytes:
                    try:
                        found[path] = tar.extractfile(member).read().decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                    if len(found) == len(wanted):
                        break
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001 - what was read before the failure is still valid
        pass
    finally:
        if reader is not None:
            reader.close()
    return found


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
