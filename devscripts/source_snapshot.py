"""Copy and verify committed source files for parallel tests."""
import hashlib
import io
import json
import os
import sys
import subprocess


# Bound the in-memory batch to this repository's intended scale.
MAX_SOURCE_BYTES = 128 * 1024 * 1024


def materialize_commit(root, head, destination):
    """Return a manifest of raw committed blobs, without checkout filters."""
    object_format = subprocess.check_output(
        ['git', 'rev-parse', '--show-object-format'], cwd=root, text=True).strip()
    if object_format not in ('sha1', 'sha256'):
        raise ValueError(f'unsupported Git object format: {object_format}')
    listing = subprocess.check_output(['git', '--no-replace-objects', 'ls-tree', '-rlz', '--full-tree', head], cwd=root)
    entries = []
    for record in listing.split(b'\0'):
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, kind, oid, size = metadata.decode('ascii').split()
        path = os.fsdecode(raw_path)
        parts = path.split('/')
        if (kind != 'blob' or mode not in ('100644', '100755')
                or any(part in ('', '.', '..') or '\\' in part or ':' in part for part in parts)):
            raise ValueError(f'unsupported committed path: {path!r}')
        entries.append((path, mode, oid, int(size)))
    if not entries or sum(size for _, _, _, size in entries) > MAX_SOURCE_BYTES:
        raise ValueError('empty or oversized source snapshot')
    batch = subprocess.check_output(
        ['git', '--no-replace-objects', 'cat-file', '--batch'], cwd=root,
        input=''.join(f'{oid}\n' for _, _, oid, _ in entries).encode('ascii'))
    stream = io.BytesIO(batch)
    destination.mkdir()
    manifest = []
    for path, mode, oid, size in entries:
        header = stream.readline().decode('ascii').strip().split()
        if header != [oid, 'blob', str(size)]:
            raise ValueError(f'Git blob header mismatch: {path!r}')
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b'\n':
            raise ValueError(f'truncated Git blob: {path!r}')
        object_hash = hashlib.new(object_format, usedforsecurity=False)
        object_hash.update(f'blob {size}\0'.encode('ascii'))
        object_hash.update(data)
        if object_hash.hexdigest() != oid:
            raise ValueError(f'Git blob identity mismatch: {path!r}')
        target = destination.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(data)
        if os.name != 'nt':
            target.chmod(0o755 if mode == '100755' else 0o644)
        manifest.append((path, mode, size, hashlib.sha256(data).hexdigest()))
    if stream.read(1):
        raise ValueError('unexpected trailing Git blob data')
    return manifest


def manifest_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


def verify_snapshot(root, manifest):
    """Check each materialised input before executing trusted test code."""
    for path, mode, size, digest in manifest:
        target = root.joinpath(*path.split('/'))
        if (target.is_symlink() or not target.is_file()
                or target.stat().st_size != size or hashlib.sha256(target.read_bytes()).hexdigest() != digest):
            raise ValueError(f'source snapshot mismatch: {path!r}')
        if sys.platform != 'cygwin' and os.name != 'nt' and bool(target.stat().st_mode & 0o111) != (mode == '100755'):
            raise ValueError(f'source mode mismatch: {path!r}')
