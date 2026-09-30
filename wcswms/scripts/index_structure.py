"""Read-only fingerprint index; never parses native CAD as a mesh."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
files=[]
for p in sorted((ROOT/'结构').iterdir()):
    if not p.name.startswith('~') and p.is_file() and p.suffix.upper() in ('.SLDASM','.SLDPRT'):
        files.append(dict(name=p.name,type='装配体' if p.suffix.upper()=='.SLDASM' else '零件',bytes=p.stat().st_size,sha256=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()))
output=ROOT/'assets/structure';output.mkdir(parents=True,exist_ok=True)
metadata=output/'assembly-metadata.json'
stl=output/'sample-room.glb'
manifest=dict(assembly_count=sum(f['type']=='装配体' for f in files),part_count=sum(f['type']=='零件' for f in files),files=files,previews=[],note='原始 SolidWorks 文件已建立校验索引。完整真实装配已建立逐实例映射和候选运动绑定；坐标比例和现场库位仍待校准。',cad_url='/assets/structure/sample-room.glb' if stl.exists() else None,metadata=json.loads(metadata.read_text(encoding='utf-8-sig')) if metadata.exists() else None)
report=output/'mesh-report.json'
manifest['twin_mapping_url']='/assets/structure/twin-map.json'
manifest['instance_mapping_url']='/assets/structure/instance-map.json'
manifest['assembly_mapping_url']='/assets/structure/assembly-map.json'
manifest['mesh_report']=json.loads(report.read_text(encoding='utf-8')) if report.exists() else None
(output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Indexed {len(files)} CAD files; native export: {stl.exists()}')


