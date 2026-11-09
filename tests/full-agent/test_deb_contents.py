"""deb_contents: the files a frozen dependency package installs, read from the
case's .deb files without installing anything. A fake .deb is built here with
the standard library (ar + gzip tar). No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

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
            original = deb_archive._decompress
            deb_archive._decompress = lambda name, data: None
            try:
                info = deb_archive.inspect(deb)
            finally:
                deb_archive._decompress = original
            self.assertIn("zstd-compressed and no decompressor", info.note)
            self.assertIn("which is an empty package", info.note)
