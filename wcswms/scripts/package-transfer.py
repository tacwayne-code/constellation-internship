"""Create a verified migration archive without machine-local runtimes or live PLC state."""
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'releases'
out.mkdir(exist_ok=True)
stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
archive = out / f'wcswms-commissioning-{stamp}.zip'
excluded = {'node_modules', '__pycache__', '.pytest_cache', '.git', '.venv'}
folders = ['backend', 'plc', 'config', 'docs', 'scripts', 'tests', 'assets', 'web',
           '电气', '结构', 'data/siemens/projects/v20-simulation_V20',
           'data/siemens/exports/20260922-110939', 'data/siemens/analysis']
files = {p for folder in folders for p in (ROOT / folder).rglob('*')
         if p.is_file() and not (set(p.relative_to(ROOT).parts) & excluded)
         and p.suffix.lower() not in {'.pyc', '.lck', '.lock', '.dmp'}}
files.update(p for p in ROOT.iterdir() if p.is_file())
manifest = []
def add(z, name, content):
    z.writestr(name, content)
    manifest.append({'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
dbcopy = out / f'demo-snapshot-{stamp}.sqlite3'
with sqlite3.connect((ROOT / 'data/warehouse.sqlite3').as_uri() + '?mode=ro', uri=True) as source:
    with sqlite3.connect(dbcopy) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as z:
    for p in sorted(files):
        add(z, p.relative_to(ROOT).as_posix(), p.read_bytes())
    add(z, 'transfer-evidence/warehouse-demo.sqlite3', dbcopy.read_bytes())
    for name in ['virtual-baseline-result.json', 'virtual-ack-result.json', 'laser-feedback-test.json', 'readiness-report.json']:
        p = ROOT / 'data/siemens' / name
        if p.exists(): add(z, 'transfer-evidence/' + name, p.read_bytes())
    z.writestr('PACKAGE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for row in manifest:
        assert hashlib.sha256(z.read(row['path'])).hexdigest() == row['sha256'], row['path']
digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'files': len(manifest), 'sha256': digest, 'verified': True}, ensure_ascii=False), flush=True)
