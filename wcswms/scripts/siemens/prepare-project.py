from pathlib import Path
import shutil, hashlib, json
root=Path.cwd()
src=root/'电气/PLC程序'
dst=root/'data/siemens/projects/v20-simulation'
if not dst.exists():
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(src,dst)
records=[]
for p in sorted(src.rglob('*')):
    if p.is_file():
        rel=p.relative_to(src)
        with p.open('rb') as stream: sha=hashlib.file_digest(stream,'sha256').hexdigest()
        q=dst/rel
        with q.open('rb') as stream: copied=hashlib.file_digest(stream,'sha256').hexdigest()
        records.append(dict(path=rel.as_posix(),bytes=p.stat().st_size,sha256=sha,copy_matches=sha==copied))
report=dict(source=str(src),working_copy=str(dst),source_modified=False,files=records,all_match=all(r['copy_matches'] for r in records))
(root/'data/siemens/project-copy-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(working_copy=str(dst),files=len(records),bytes=sum(r['bytes'] for r in records),all_match=report['all_match']),ensure_ascii=False))
