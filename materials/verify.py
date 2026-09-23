#!/usr/bin/env python3
"""Verify the uploaded archive or an extracted materials directory."""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, os, posixpath, shutil, subprocess, tarfile

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def safe_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe archive path: ' + name)
    return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Verify extracted files instead of archive contents')
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    manifest = json.loads((base / 'manifest.json').read_text())
    entries = {entry['path']: entry for entry in manifest['entries']}
    if any(name.split('/')[0].startswith('buildbench-starter-kit') for name in entries):
        raise ValueError('Starter kit unexpectedly present in snapshot')
    if args.directory:
        root = args.directory.resolve()
        for name, entry in entries.items():
            path = root.joinpath(*safe_path(name).parts)
            kind = entry['type']
            if kind == 'symlink':
                assert path.is_symlink() and os.readlink(path) == entry['target'], name
            elif kind == 'directory':
                assert path.is_dir() and not path.is_symlink(), name
            else:
                assert path.is_file() and not path.is_symlink(), name
                assert path.stat().st_size == entry['bytes'], name
                assert sha256_file(path) == entry['sha256'], name
        print('Verified extracted directory:', root)
    else:
        archive = base / manifest['archive']
        assert archive.stat().st_size == manifest['archive_bytes'], 'Archive size mismatch; run git lfs pull'
        assert sha256_file(archive) == manifest['archive_sha256'], 'Archive checksum mismatch'
        zstd = shutil.which('zstd')
        if not zstd:
            raise SystemExit('zstd is required to verify archive contents')
        process = subprocess.Popen([zstd, '-d', '-c', str(archive)], stdout=subprocess.PIPE)
        seen = {}
        with tarfile.open(fileobj=process.stdout, mode='r|') as stream:
            for member in stream:
                safe_path(member.name)
                assert member.name not in seen, 'Duplicate entry: ' + member.name
                entry = entries[member.name]
                if member.issym():
                    assert entry['type'] == 'symlink' and member.linkname == entry['target'], member.name
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                    safe_path(target)
                elif member.isdir():
                    assert entry['type'] == 'directory', member.name
                elif member.islnk():
                    safe_path(member.linkname)
                    assert entry['type'] == 'file', member.name
                    assert seen[member.linkname]['sha256'] == entry['sha256'], member.name
                elif member.isfile():
                    assert entry['type'] == 'file' and member.size == entry['bytes'], member.name
                    digest = hashlib.sha256()
                    with stream.extractfile(member) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            digest.update(chunk)
                    assert digest.hexdigest() == entry['sha256'], member.name
                else:
                    raise ValueError('Unsupported archive entry: ' + member.name)
                seen[member.name] = entry
        process.stdout.close()
        assert process.wait() == 0, 'zstd decompression failed'
        assert seen.keys() == entries.keys(), 'Archive inventory mismatch'
        print('Verified archive checksum and every archived file')
    print(json.dumps({'entry_counts': manifest['entry_counts'], 'archive_sha256': manifest['archive_sha256']}, indent=2))

if __name__ == '__main__':
    main()
