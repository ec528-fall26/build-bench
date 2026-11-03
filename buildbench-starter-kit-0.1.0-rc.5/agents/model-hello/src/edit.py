"""Deletion-only hello tool; never inserts or executes model-generated commands."""
import hashlib
import json
from pathlib import Path
import secrets
import stat

SPEC = 'input/buildbench-hello.spec'


def apply_edit(repo, arguments):
    values = json.loads(arguments)
    if not isinstance(values, dict) or set(values) != {'path', 'old_text', 'new_text'}:
        raise ValueError('Invalid edit fields')
    if values['path'] != SPEC or any(not isinstance(values[k], str) for k in values):
        raise ValueError('Edit outside allowlist')
    old, new = values['old_text'], values['new_text']
    if not old or old == new or max(len(old), len(new)) > 4096:
        raise ValueError('Invalid edit size or no-op')
    if new != '':
        raise ValueError('This demo accepts deletions only')
    if '%' in new or any(ord(c) < 32 and c not in '\n\t' for c in new):
        raise ValueError('Unsafe replacement')
    if any(line.strip() and not line.lstrip().startswith('#') for line in new.splitlines()):
        raise ValueError('Only deletions/comments permitted in this hello example')
    repo = Path(repo).resolve(strict=True)
    target = repo / SPEC
    entry = target.lstat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or not target.resolve().is_relative_to(repo):
        raise ValueError('Unsafe edit target')
    content = target.read_text(encoding='utf-8')
    if len(content.encode()) > 8192 or '\x00' in content:
        raise ValueError('Unexpected source size')
    start, end = content.index('\n%build\n') + len('\n%build\n'), content.index('\n%install\n')
    if content.count(old) != 1:
        raise ValueError('Edit must match exactly once')
    offset = content.index(old)
    if offset < start or offset + len(old) > end:
        raise ValueError('Edit outside build section')
    updated = content.replace(old, new, 1)
    if updated == content or not updated.endswith('\n'):
        raise ValueError('No repair or missing final newline')
    with target.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(updated)
    return {'path': SPEC, 'before_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(updated.encode()).hexdigest(), 'receipt': 'BB_EDIT_' + secrets.token_hex(8)}
