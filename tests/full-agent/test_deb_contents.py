"""deb_contents: the files a frozen dependency package installs, read from the
case's .deb files without installing anything. A fake .deb is built here with
the standard library (ar + gzip tar). No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import gzip
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import deb_archive, tools  # noqa: E402
from src.contracts import ToolRequest  # noqa: E402


def _tar_gz(entries: dict) -> bytes:
    """entries: path -> bytes (file) or ("link", target)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path, value in entries.items():
            info = tarfile.TarInfo("./" + path)
            if isinstance(value, tuple):
                info.type = tarfile.SYMTYPE
                info.linkname = value[1]
                tar.addfile(info)
            else:
                info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
    return gzip.compress(buffer.getvalue())


def _tar(entries: dict) -> bytes:
    return gzip.decompress(_tar_gz(entries))


def _ar(members: dict) -> bytes:
    out = b"!<arch>\n"
    for name, blob in members.items():
        header = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(blob):<10}`\n".encode()
        out += header + blob + (b"\n" if len(blob) % 2 else b"")
    return out


def make_deb(path: Path, package: str, version: str, arch: str, files: dict) -> Path:
    control = f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n".encode()
    deb = _ar({"debian-binary": b"2.0\n", "control.tar.gz": _tar_gz({"control": control}),
               "data.tar.gz": _tar_gz(files)})
    target = path / f"{package}_{version}_{arch}.deb"
    target.write_bytes(deb)
    return target


def make_deb_as(path: Path, package: str, data_name: str, data_blob: bytes, arch="amd64") -> Path:
    """A deb whose data member has the given name and (already compressed) bytes."""
    control = f"Package: {package}\nVersion: 1\nArchitecture: {arch}\n".encode()
    deb = _ar({"debian-binary": b"2.0\n", "control.tar.gz": _tar_gz({"control": control}), data_name: data_blob})
    target = path / f"{package}_1_{arch}.deb"
    target.write_bytes(deb)
    return target


class Worktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input").mkdir()
        deps = self.root / "dependencies" / "ubuntu-noble-arm64"
        deps.mkdir(parents=True)
        make_deb(deps, "libfiat-dev", "1.2.0-1", "arm64", {
            "usr/lib/aarch64-linux-gnu/cmake/fiat/fiat-targets.cmake":
                b'set_target_properties(fiat PROPERTIES\n  INTERFACE_INCLUDE_DIRECTORIES "/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat"\n)\n',
            "usr/include/aarch64-linux-gnu/fortran/gfortran-mod-15/fiat/yomhook.mod": b"mod",
            "usr/lib/aarch64-linux-gnu/libfiat.so": ("link", "libfiat.so.0d"),
        })
        make_deb(deps, "libbigwig0t64", "0.4.8+dfsg-2", "arm64", {})
        make_deb(deps, "libbigwig-dev", "0.4.8+dfsg-2", "arm64", {"usr/lib/aarch64-linux-gnu/libBigWig.a": b"!<arch>\n"})

    def run_tool(self, **args):
        return tools.run_tool(ToolRequest("deb_contents", args), self.root)


class Listing(Worktree):
    def test_lists_files_sizes_and_symlinks(self):
        text = self.run_tool(package="libfiat-dev").text
        self.assertIn("libfiat-dev 1.2.0-1 (arm64)", text)
        self.assertIn("usr/include/aarch64-linux-gnu/fortran/gfortran-mod-15/fiat/yomhook.mod (3 bytes)", text)
        self.assertIn("usr/lib/aarch64-linux-gnu/libfiat.so -> libfiat.so.0d", text)

    def test_filter_narrows_the_list(self):
        text = self.run_tool(package="libfiat-dev", filter="cmake").text
        self.assertIn("fiat-targets.cmake", text)
        self.assertNotIn("yomhook.mod", text)

    def test_an_empty_runtime_package_is_visible(self):
        text = self.run_tool(package="libbigwig0t64").text
        self.assertIn("[ships no files]", text)

    def test_reads_one_text_file(self):
        text = self.run_tool(package="libfiat-dev",
                             path="usr/lib/aarch64-linux-gnu/cmake/fiat/fiat-targets.cmake").text
        self.assertIn('INTERFACE_INCLUDE_DIRECTORIES "/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat"', text)

    def test_prefix_match_and_unknown_package(self):
        self.assertIn("libbigwig0t64", self.run_tool(package="libbigwig").text)
        self.assertIn("no dependency package named like 'nothing'", self.run_tool(package="nothing").text)

    def test_missing_file_and_bad_arguments(self):
        self.assertIn("is not in the package", self.run_tool(package="libfiat-dev", path="usr/nope").text)
        self.assertIn("missing argument: package", self.run_tool().text)
        self.assertIn("invalid argument type", self.run_tool(package="libfiat-dev", filter=3).text)


class WithoutDebs(unittest.TestCase):
    def test_says_so_and_falls_back_to_the_lock_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input").mkdir()
            (root / "config").mkdir()
            (root / "config/dependency-lock.json").write_text(json.dumps({"dependencies": [
                {"package": "libbigwig0t64", "version": "0.4.8+dfsg-2", "arch": "amd64"}]}))
            text = tools.run_tool(ToolRequest("deb_contents", {"package": "libbigwig"}), root).text
            self.assertIn("no dependencies/ directory", text)
            self.assertIn("libbigwig0t64 0.4.8+dfsg-2 [amd64]", text)


class ArParsing(unittest.TestCase):
    def test_members_and_zstd_without_decompressor_note(self):
        blob = _ar({"debian-binary": b"2.0\n", "data.tar.zst": b"\x28\xb5\x2f\xfd" + b"\x00" * 20})
        names = [m[0] for m in deb_archive.ar_members(blob)]
        self.assertEqual(names, ["debian-binary", "data.tar.zst"])
        with tempfile.TemporaryDirectory() as temp:
            deb = Path(temp) / "libx0_1_amd64.deb"
            deb.write_bytes(blob)
            original = deb_archive._open_stream
            deb_archive._open_stream = lambda name, data: None
            try:
                info = deb_archive.inspect(deb)
            finally:
                deb_archive._open_stream = original
            self.assertIn("zstd-compressed and no decompressor", info.note)
            self.assertIn("which is an empty package", info.note)


try:
    import zstandard
except ImportError:  # the wheel is optional: every path must work without it
    zstandard = None


@unittest.skipIf(zstandard is None, "zstandard is not installed")
class ZstdPath(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_lists_a_two_frame_zstd_member(self):
        raw = _tar({"usr/lib/libx.so.1": b"x" * 10, "usr/lib/libx.so": ("link", "libx.so.1")})
        half = 1024  # split mid-archive: a reader that stops at the first frame loses the rest
        compressor = zstandard.ZstdCompressor()
        blob = compressor.compress(raw[:half]) + compressor.compress(raw[half:])
        deb = make_deb_as(self.dir, "libx1", "data.tar.zst", blob)
        info = deb_archive.inspect(deb)
        self.assertEqual(info.note, "")
        self.assertIn("usr/lib/libx.so -> libx.so.1", info.entries)
        self.assertIn("usr/lib/libx.so.1 (10 bytes)", info.entries)

    def test_empty_zstd_package_and_read_text(self):
        deb = make_deb_as(self.dir, "libempty0", "data.tar.zst",
                          zstandard.ZstdCompressor().compress(_tar({})))
        self.assertEqual(deb_archive.inspect(deb).note, "ships no files")
        deb = make_deb_as(self.dir, "libt0", "data.tar.zst",
                          zstandard.ZstdCompressor().compress(_tar({"etc/x.conf": b"hello\n"})))
        self.assertEqual(deb_archive.read_text(deb, "etc/x.conf"), ("hello\n", ""))

    def test_output_cap_stops_a_bomb(self):
        raw = _tar({"big.bin": b"\0" * 200_000})
        deb = make_deb_as(self.dir, "bomb0", "data.tar.zst", zstandard.ZstdCompressor().compress(raw))
        with unittest.mock.patch.object(deb_archive, "MAX_DECOMPRESSED_BYTES", 50_000):
            info = deb_archive.inspect(deb)
            text, note = deb_archive.read_text(deb, "big.bin")
        self.assertIn("read limit", info.note)
        self.assertIsNone(text)

    def test_corrupt_zstd_is_a_note_not_an_exception(self):
        deb = make_deb_as(self.dir, "bad0", "data.tar.zst", b"\x28\xb5\x2f\xfd" + b"\xff" * 40)
        info = deb_archive.inspect(deb)
        self.assertIn("cannot list data.tar.zst", info.note)
        (self.dir / "dependencies/d").mkdir(parents=True)
        (self.dir / "input").mkdir()
        deb.rename(self.dir / "dependencies/d" / deb.name)
        result = tools.run_tool(ToolRequest("deb_contents", {"package": "bad0"}), self.dir)
        self.assertIn("cannot list", result.text)


class Robustness(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_without_zstandard_a_zst_deb_gets_the_sizes_only_note(self):
        deb = make_deb_as(self.dir, "libz0", "data.tar.zst", b"\x28\xb5\x2f\xfd" + b"\x00" * 20)
        with unittest.mock.patch.object(deb_archive, "_zstandard", lambda: None), \
                unittest.mock.patch("shutil.which", lambda name: None):
            try:
                from compression import zstd  # noqa: F401
            except ImportError:
                info = deb_archive.inspect(deb)
                self.assertIn("no decompressor", info.note)
                self.assertIn("empty package", info.note)

    def test_truncated_xz_never_raises(self):
        import lzma
        blob = lzma.compress(_tar({"usr/a": os.urandom(40_000), "usr/b": b"b"}))
        blob = blob[:len(blob) // 2]
        deb = make_deb_as(self.dir, "libxz0", "data.tar.xz", blob)
        info = deb_archive.inspect(deb)
        self.assertTrue(info.note)  # LZMAError / EOFError became a note
        self.assertIsNone(deb_archive.read_text(deb, "usr/b")[0])

    def test_cache_inflates_each_deb_once(self):
        deb = make_deb(self.dir, "libc0", "1", "amd64", {"usr/lib/libc.so": ("link", "libc.so.0")})
        cache = {}
        first = deb_archive.inspect(deb, cache=cache)
        with unittest.mock.patch.object(deb_archive, "_open_stream", side_effect=AssertionError("re-read")):
            second = deb_archive.inspect(deb, cache=cache)
            filtered = deb_archive.inspect(deb, pattern="nomatch", cache=cache)
        self.assertEqual(first.entries, second.entries)
        self.assertEqual(filtered.note, "ships no files matching 'nomatch'")

    def test_deb_contents_has_its_own_time_limit(self):
        self.assertGreater(tools.MAX_DEB_SECONDS, tools.MAX_TOOL_SECONDS)


if __name__ == "__main__":
    unittest.main()
