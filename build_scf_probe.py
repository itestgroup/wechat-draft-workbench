"""Build SCF code and Linux runtime dependencies without credentials/local data."""
import hashlib
import json
from pathlib import Path
from scripts.deploy_scf import build_package, PACKAGE_FILES

ROOT = Path(__file__).resolve().parent
FILES = PACKAGE_FILES


def build():
    directory = ROOT / 'dist'
    directory.mkdir(exist_ok=True)
    target = directory / 'scf-probe.zip'
    runtime = ROOT / '.build/scf-runtime'
    target.write_bytes(build_package(ROOT, runtime))
    manifest = {'artifact': target.name,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                'files': list(FILES), 'runtime_dependencies': 'scripts/requirements-runtime.txt',
                'handler': 'scf_probe.main_handler',
                'scope': 'Console read-only check plus authenticated article API; draft sync disabled unless explicitly configured.'}
    (directory / 'scf-probe-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(target))
    return target


if __name__ == '__main__':
    build()
