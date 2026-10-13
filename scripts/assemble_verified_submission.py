"""Reconstruct the reviewed archive from exact source bytes and ZIP metadata."""
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'submission-archive-manifest.json').read_text(encoding='utf-8'))
assets = Path(sys.argv[1])
destination = Path(sys.argv[2])
with zipfile.ZipFile(destination, 'w') as archive:
    for entry in manifest['entries']:
        if 'repo_path' in entry:
            data = (root / entry['repo_path']).read_bytes()
        elif 'asset' in entry:
            data = (assets / entry['asset']).read_bytes()
        else:
            data = base64.b64decode(entry['base64'])
        assert len(data) == entry['bytes']
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['name']
        info = zipfile.ZipInfo(entry['name'], tuple(entry['date_time']))
        for key in ['compress_type', 'create_system', 'create_version', 'extract_version', 'external_attr', 'internal_attr']:
            setattr(info, key, entry[key])
        info.extra = base64.b64decode(entry['extra'])
        info.comment = base64.b64decode(entry['comment'])
        archive.writestr(info, data, compresslevel=6)
assert destination.stat().st_size == manifest['bytes']
assert hashlib.sha256(destination.read_bytes()).hexdigest() == manifest['sha256']
print('PASS: reconstructed archive is byte-for-byte identical to the reviewed submission.')
