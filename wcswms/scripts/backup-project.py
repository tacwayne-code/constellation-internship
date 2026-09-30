"""Create and verify local reinstall archives; refuses a running lab."""
from pathlib import Path
import hashlib
import json
import socket
import sqlite3
import zipfile
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    for port in (8765,1102):
        with socket.socket() as connection:
            connection.settimeout(.5)
            if connection.connect_ex(('127.0.0.1',port))==0:
                raise SystemExit(f'Stop the lab before backup: port {port} is active')
    with sqlite3.connect(ROOT/'data/warehouse.sqlite3') as db:
        check=db.execute('PRAGMA integrity_check').fetchone()[0]
        if check!='ok': raise RuntimeError(check)
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    out=ROOT/'backups'/stamp
    out.mkdir(parents=True)
    excluded={'.venv','node_modules','.pnpm-store','.pytest_cache','__pycache__','.git','backups'}
    files=[];dumps=[]
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file(): continue
        rel=path.relative_to(ROOT)
        if any(part in excluded for part in rel.parts): continue
        if path.name in ('server.lock','warehouse.sqlite3-shm','warehouse.sqlite3-wal') or path.suffix=='.pyc': continue
        (dumps if path.suffix.lower()=='.dmp' else files).append(path)
    summaries=[]
    for label,group in [('main',files),('crash-dumps',dumps)]:
        archive=out/f'wcswms-{label}-{stamp}.zip'
        manifest=[]
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
            for path in group:
                rel=path.relative_to(ROOT).as_posix()
                before=path.stat()
                digest=sha(path)
                z.write(path,'wcswms/'+rel)
                if path.stat().st_mtime_ns!=before.st_mtime_ns or path.stat().st_size!=before.st_size:
                    raise RuntimeError(f'File changed during backup: {rel}')
                manifest.append({'path':'wcswms/'+rel,'bytes':before.st_size,'sha256':digest})
            z.writestr('wcswms/BACKUP-MANIFEST.json',json.dumps({'created':stamp,'kind':label,'sqlite_integrity':check,'files':manifest},ensure_ascii=False,indent=2))
        print(f'Created {archive.name}; validating {len(manifest)} files',flush=True)
        with zipfile.ZipFile(archive) as z:
            for record in manifest:
                with z.open(record['path']) as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=record['sha256']:raise RuntimeError('Archive hash mismatch: '+record['path'])
        summaries.append({'file':archive.name,'bytes':archive.stat().st_size,'files':len(manifest),'sha256':sha(archive),'all_file_hashes_verified':True})
    (out/'backup-verification.json').write_text(json.dumps({'sqlite_integrity':check,'archives':summaries},indent=2),encoding='utf-8')
    (out/'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['file']}\n" for r in summaries),encoding='ascii')
    (out/'重装恢复说明.md').write_bytes((ROOT/'docs/重装恢复说明.md').read_bytes())
    print(json.dumps({'directory':str(out),'archives':summaries},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
