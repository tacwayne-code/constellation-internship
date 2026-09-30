"""Export every extracted CAD instance, preserving source identity and twin bindings.

Reads the original SolidWorks tessellation and never writes source CAD files.
Motion bindings are reviewable in config/twin-map.json; they are uncalibrated.
"""
import array
import hashlib
import itertools
import json
import re
import struct
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/cad-extract'
OUTPUT = ROOT / 'assets/structure'
HARDWARE = re.compile('螺|垫圈|轴承|挡圈|卡簧|链条|圆头键|弹簧')
MACHINE = 'LG2-200000-1/'
CARRIAGE = MACHINE + 'LGZY-240000-1/'
FORK = CARRIAGE + 'LGZY-260000-1/'


def classify(item):
    name = item['name']
    if item['file'] == '料箱.SLDPRT':
        return 'design_load'
    if name.startswith(FORK + 'LGZY-264000-1/'):
        return 'fork_tip'
    if name.startswith(FORK + 'LGZY-263000-1/'):
        return 'fork_mid'
    if name.startswith(CARRIAGE):
        return 'lift'
    if name.startswith(MACHINE):
        return 'travel'
    if name.startswith('货架'):
        return 'static'
    return 'unclassified'


def sw_matrix(a):
    scale = a[12]
    return [a[0]*scale, a[1]*scale, a[2]*scale, 0,
            a[3]*scale, a[4]*scale, a[5]*scale, 0,
            a[6]*scale, a[7]*scale, a[8]*scale, 0,
            a[9], a[10], a[11], 1]


def main():
    scene = json.loads((SOURCE / 'scene.json').read_text(encoding='utf-8-sig'))
    instances = scene['instances']
    gltf = {'asset': {'version': '2.0', 'generator': 'WCS complete CAD/twin exporter'},
            'scene': 0, 'scenes': [{'nodes': []}], 'nodes': [], 'meshes': [],
            'accessors': [], 'bufferViews': [], 'buffers': [], 'materials': []}
    for name, color in [('rack', [.25,.40,.57,1]), ('machine', [.66,.70,.75,1]),
                        ('load', [.10,.65,.68,1]), ('hardware', [.45,.51,.59,1])]:
        gltf['materials'].append({'name': name, 'doubleSided': True,
            'pbrMetallicRoughness': {'baseColorFactor': color, 'metallicFactor': .25, 'roughnessFactor': .6}})
    binary = bytearray()
    cache = {}

    def accessor(raw, component, count, typ, mins=None, maxs=None, target=34962):
        binary.extend(b'\0' * ((-len(binary)) % 4))
        offset = len(binary)
        binary.extend(raw)
        view = len(gltf['bufferViews'])
        gltf['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw), 'target': target})
        value = {'bufferView': view, 'componentType': component, 'count': count, 'type': typ}
        if mins is not None:
            value.update(min=mins, max=maxs)
        gltf['accessors'].append(value)
        return len(gltf['accessors'])-1

    for item in instances:
        key = item['mesh']
        if key in cache:
            continue
        data = (SOURCE / key).read_bytes()
        vertices, indices, lookup = array.array('f'), array.array('I'), {}
        mins, maxs = [float('inf')]*3, [float('-inf')]*3
        for xyz in struct.iter_unpack('<3f', data):
            idx = lookup.get(xyz)
            if idx is None:
                idx = len(lookup)
                lookup[xyz] = idx
                vertices.extend(xyz)
                for j, value in enumerate(xyz):
                    mins[j] = min(mins[j], value)
                    maxs[j] = max(maxs[j], value)
            indices.append(idx)
        pos = accessor(vertices.tobytes(), 5126, len(vertices)//3, 'VEC3', mins, maxs)
        ind = accessor(indices.tobytes(), 5125, len(indices), 'SCALAR', target=34963)
        cache[key] = (pos, ind, len(indices)//3, mins, maxs)

    fingerprints = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (ROOT/'结构').iterdir()
                    if p.is_file() and not p.name.startswith('~') and p.suffix.upper() in ('.SLDPRT', '.SLDASM')}
    mesh_cache, records = {}, []
    triangles = 0
    for index, item in enumerate(instances):
        hardware = bool(HARDWARE.search(item['file']))
        group = classify(item)
        material = 2 if group == 'design_load' else 3 if hardware else 0 if group == 'static' else 1
        key = (item['mesh'], material)
        if key not in mesh_cache:
            pos, ind, count, *_ = cache[item['mesh']]
            mesh_cache[key] = len(gltf['meshes'])
            gltf['meshes'].append({'name': item['file'], 'primitives': [{'attributes': {'POSITION': pos}, 'indices': ind, 'material': material}]})
        instance_id = 'cad-' + hashlib.sha256(item['name'].encode()).hexdigest()[:16]
        extras = {'instance_id': instance_id, 'source_path': item['name'], 'source_file': item['file'],
                  'motion_group': group, 'hardware': hardware, 'assembly_path': item['name'].rsplit('/',1)[0] if '/' in item['name'] else '样板间总装'}
        matrix = sw_matrix(item['transform'])
        gltf['scenes'][0]['nodes'].append(index)
        gltf['nodes'].append({'name': instance_id, 'mesh': mesh_cache[key], 'matrix': matrix, 'extras': extras})
        records.append({**extras, 'source_sha256': fingerprints.get(item['file']), 'node_index': index,
                        'matrix': matrix, 'triangle_count': cache[item['mesh']][2]})
        triangles += cache[item['mesh']][2]
    binary.extend(b'\0' * ((-len(binary)) % 4))
    gltf['buffers'] = [{'byteLength': len(binary)}]
    js = json.dumps(gltf, ensure_ascii=False, separators=(',', ':')).encode()
    js += b' ' * ((-len(js)) % 4)
    glb = struct.pack('<4sII', b'glTF', 2, 12+8+len(js)+8+len(binary)) + struct.pack('<I4s', len(js), b'JSON') + js + struct.pack('<I4s', len(binary), b'BIN\x00') + binary
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/'sample-room.glb').write_bytes(glb)

    totes = [item for item in instances if item['file'] == '料箱.SLDPRT']
    axes = [sorted(set(round(item['transform'][9+k], 6) for item in totes)) for k in range(3)]
    observed = {}
    for item in totes:
        position = tuple(round(item['transform'][9+k], 6) for k in range(3))
        observed.setdefault(position, []).append(item['name'])
    candidates = []
    for zi, z in enumerate(axes[2]):
        for xi, x in enumerate(axes[0]):
            for yi, y in enumerate(axes[1]):
                path = observed.get((x,y,z))
                candidates.append({'id': f"{'L' if zi == 0 else 'R'}-{xi+1:02}-{yi+1:02}", 'position_m': [x,y,z],
                    'evidence': 'CAD_TOTE' if path else 'GRID_INTERPOLATION', 'source_path': path[0] if path else None, 'source_paths':path or [], 'approved': False})
    layout = json.loads((ROOT/'config/layout.json').read_text(encoding='utf-8'))
    active = [c['id'] for c in candidates if int(c['id'][2:4]) <= layout['columns'] and int(c['id'][5:7]) <= layout['levels']]
    assembly_paths = sorted({prefix for name in [i['name'] for i in instances] + scene['missing']
        for count in range(1, len(name.split('/'))) for prefix in ['/'.join(name.split('/')[:count])]})
    assemblies = []
    for path in assembly_paths:
        source_file = re.sub(r'-\d+$', '', path.split('/')[-1]) + '.SLDASM'
        assemblies.append({'assembly_path':path, 'source_file':source_file,
            'source_sha256':fingerprints.get(source_file), 'source_resolved':source_file in fingerprints,
            'descendant_instances':sum(i['name'].startswith(path+'/') for i in instances)})
    sensor_pattern = re.compile('TL_|TL-|D4MC|E3Z|接近|AMS300|VBP31|急停|触摸屏|报警灯')
    sensors = [{key:r[key] for key in ('instance_id','source_path','source_file','motion_group')}
               | {'io_address':None,'calibrated':False} for r in records if sensor_pattern.search(r['source_file'])]
    mapping = {
        'version': 1, 'calibrated': False, 'units': 'metres',
        'source_assembly': '样板间总装.SLDASM', 'source_scene_sha256': hashlib.sha256((SOURCE/'scene.json').read_bytes()).hexdigest(),
        'model_url': '/assets/structure/sample-room.glb', 'instances_url': '/assets/structure/instance-map.json',
        'note': '原始CAD面网格完整映射。运动分组由装配层级与几何推断，库位与轴零点尚未现场校准；不得直接作为真实PLC定位参数。',
        'coverage': {'source_files': len(fingerprints), 'part_files': len({i['file'] for i in instances}), 'assembly_instances':len(assemblies), 'sensor_candidates':len(sensors),
            'rendered_instances': len(instances), 'extracted_instances': len(instances), 'missing_references': len(scene['missing']),
            'hardware_instances': sum(r['hardware'] for r in records), 'group_counts': dict(Counter(r['motion_group'] for r in records)),
            'observed_load_instances': len(totes), 'observed_location_count':len(observed), 'duplicate_design_load_instances':len(totes)-len(observed), 'candidate_locations': len(candidates), 'operational_demo_locations': len(active)},
        'coordinate_mapping': {
            'kind': 'UNVALIDATED_DEMO_AFFINE', 'source': 'state.device.x/y/z',
            'x': {'scale': .47/layout['column_pitch_m'], 'offset_m': axes[0][0]-.47},
            'y': {'scale': .375/layout['level_pitch_m'], 'offset_m': axes[1][0]-.4*.375/layout['level_pitch_m']},
            'z': {'scale': .68/layout['aisle_half_width_m'], 'offset_m': sum(axes[2])/2},
            'explanation': '保留现有48演示库位任务，将演示坐标按等比例显示至CAD前6列4层。比例转换只用于可视化，不改写PLC坐标。'},
        'motion_reference_m': {'travel_x': 2.984742153, 'lift_y': 6.1026, 'aisle_z': 7.381170946,
            'fork_mid_extension': .34, 'fork_tip_extension': .68},
        'sensor_candidates':sensors, 'assembly_map_url':'/assets/structure/assembly-map.json',
        'motion_groups': [
            {'id':'static','label':'货架、地轨','axes':[],'evidence':'货架总总装装配路径','confidence':'assembly'},
            {'id':'travel','label':'行走机体','axes':['X'],'evidence':'LG2-200000剩余节点','confidence':'inferred'},
            {'id':'lift','label':'升降载货台与固定叉座','axes':['X','Y'],'evidence':'LGZY-240000装配','confidence':'inferred'},
            {'id':'fork_mid','label':'中级伸缩叉','axes':['X','Y','Z/2'],'evidence':'LGZY-263000装配；设计伸出0.34m','confidence':'inferred'},
            {'id':'fork_tip','label':'末级伸缩叉','axes':['X','Y','Z'],'evidence':'LGZY-264000装配；设计伸出0.68m','confidence':'inferred'},
            {'id':'design_load','label':'设计料箱','axes':[],'evidence':'料箱.SLDPRT的102个装配实例','confidence':'assembly'}],
        'overlapping_design_loads':[{'position_m':list(position),'source_paths':paths} for position,paths in observed.items() if len(paths)>1],
        'candidate_axes_m': {'x':axes[0],'y':axes[1],'z':axes[2]}, 'candidate_locations':candidates,
        'operational_location_ids':active, 'missing_components':scene['missing'],
        'unmapped_instances':[r['source_path'] for r in records if r['motion_group']=='unclassified'],
        'pending_calibration':['确认实际可用层列与L/R方向','核定X/Y/Z零点、单位与编码器比例','确认载货台和多级货叉的机械联动比','补齐缺失地轨引用','确认传感器IO与可视化位置','验证碰撞包络和行程极限']}
    report = {'source':'样板间总装.SLDASM','exported_instances':len(instances),'extracted_instances':len(instances),
        'omitted_hardware_instances':0,'missing_components':scene['missing'],'mesh_count':len(mesh_cache),
        'triangle_instances':triangles,'glb_bytes':len(glb),'units':'metres','mapping_url':'/assets/structure/twin-map.json',
        'note':'全部已提取实例均导出，含紧固件/轴承/链条；面网格保持原尺寸与装配变换。运动组接收实时状态，映射尚未现场校准。'}
    for path, value in [(OUTPUT/'mesh-report.json', report),(OUTPUT/'instance-map.json', {'instances':records}),
                        (OUTPUT/'assembly-map.json', {'assemblies':assemblies}),
                        (OUTPUT/'twin-map.json',mapping),(ROOT/'config/twin-map.json',mapping)]:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**report,'coverage':mapping['coverage']},ensure_ascii=False))


if __name__ == '__main__':
    main()
