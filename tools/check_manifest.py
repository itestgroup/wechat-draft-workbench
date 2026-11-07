"""Verify the publication file inventory without reading credentials or network."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / 'UPSTREAM_MANIFEST.json').read_text(encoding='utf-8'))
    expected = manifest['files']
    tracked = set(subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')) - {''}
    if tracked != set(expected) | {'UPSTREAM_MANIFEST.json'}:
        raise SystemExit('Tracked publication inventory differs from manifest; review the export.')
    for name, digest in expected.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise SystemExit('Unsafe manifest path')
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT.resolve()) or path.is_symlink():
            raise SystemExit('Manifest paths must be regular files inside the checkout')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit('Publication hash mismatch: ' + name)
    print('Publication inventory and SHA256 verified:', len(expected), 'files.')


if __name__ == '__main__':
    main()
