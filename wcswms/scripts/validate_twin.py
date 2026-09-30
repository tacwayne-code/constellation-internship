"""Validate complete CAD export provenance, binary topology, and demo transforms."""
import array
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8-sig'))

def validate():
    source = read('data/cad-extract/scene.json')
    mapping = read('config/twin-map.json')
    assert mapping == read('assets/structure/twin-map.json'), 'Published mapping differs from configuration'
    records = read('assets/structure/instance-map.json')['instances']
    assemblies = read('assets/structure/assembly-map.json')['assemblies']
    original_files = read('assets/structure/manifest.json')['files']
    raw = (ROOT/'assets/structure/sample-room.glb').read_bytes()
    magic, version, total = struct.unpack_from('<4sII',raw)
    assert (magic,version,total) == (b'glTF',2,len(raw))
    json_bytes, kind = struct.unpack_from('<I4s',raw,12)
    assert kind == b'JSON'
    model = json.loads(raw[20:20+json_bytes])
    bin_size, kind = struct.unpack_from('<I4s',raw,20+json_bytes)
    assert kind == b'BIN\x00'
    binary = memoryview(raw)[28+json_bytes:]
    assert len(binary) == bin_size == model['buffers'][0]['byteLength']
    assert len(records) == len(model['nodes']) == len(source['instances']) == 2704
    assert len({r['instance_id'] for r in records}) == 2704
    assert len({r['source_file'] for r in records}) == 315
    assert len(original_files) == 347
    assert all(a['source_resolved'] and a['source_sha256'] for a in assemblies)
    assert len(assemblies) == 90
    fingerprints = {p['name']:p['sha256'] for p in original_files}
    for record, node, original in zip(records,model['nodes'],source['instances']):
        assert record['source_path'] == original['name'] == node['extras']['source_path']
        assert record['instance_id'] == node['name']
        assert record['source_sha256'] == fingerprints[record['source_file']]
        assert record['matrix'] == node['matrix']
        assert len(node['matrix']) == 16 and all(math.isfinite(x) for x in node['matrix'])
        assert node['matrix'][12:15] == original['transform'][9:12]
        assert node['matrix'][15] == 1
    assert sum(r['hardware'] for r in records) == 1322, 'Hardware was dropped'
    assert dict(Counter(r['motion_group'] for r in records)) == mapping['coverage']['group_counts']
    assert not mapping['unmapped_instances']
    assert mapping['missing_components'] == source['missing'] and len(source['missing']) == 1
    for mesh in model['meshes']:
        primitive = mesh['primitives'][0]
        position = model['accessors'][primitive['attributes']['POSITION']]
        index = model['accessors'][primitive['indices']]
        view = model['bufferViews'][index['bufferView']]
        indices = array.array('I',binary[view['byteOffset']:view['byteOffset']+view['byteLength']])
        assert len(indices) % 3 == 0 and max(indices) < position['count']
    candidates = mapping['candidate_locations']
    assert len(candidates) == 180 and sum(c['evidence']=='CAD_TOTE' for c in candidates) == 96
    candidate_by_id = {c['id']:c for c in candidates}
    assert len(candidate_by_id) == 180
    assert len(mapping['overlapping_design_loads']) == 6
    assert sum(len(c['source_paths']) for c in candidates) == 102
    assert len(mapping['operational_location_ids']) == 48
    assert all(not c['approved'] for c in candidates) and not mapping['calibrated']
    cfg = mapping['coordinate_mapping']; layout = read('config/layout.json')
    max_error = 0
    for location_id in mapping['operational_location_ids']:
        side, column, level = location_id.split('-')
        device = [int(column)*layout['column_pitch_m'], .4+(int(level)-1)*layout['level_pitch_m'],(-1 if side=='L' else 1)*layout['aisle_half_width_m']]
        mapped = [cfg[key]['offset_m']+value*cfg[key]['scale'] for key,value in zip('xyz',device)]
        expected = candidate_by_id[location_id]['position_m']
        max_error = max(max_error,max(abs(a-b) for a,b in zip(mapped,expected)))
    assert max_error < 1e-6
    assert all(s['io_address'] is None and not s['calibrated'] for s in mapping['sensor_candidates'])
    return {'result':'PASS','original_files':347,'mapped_parts':315,'assembly_instances':90,
            'complete_instances':2704,'retained_hardware_instances':1322,'known_missing_references':1,
            'grid_candidates':180,'observed_totes':102,'observed_unique_positions':96,'overlap_groups':6,'operational_demo_slots':48,
            'max_demo_mapping_error_m':max_error,'calibrated':False,
            'glb_sha256':hashlib.sha256(raw).hexdigest()}

if __name__ == '__main__':
    print(json.dumps(validate(),ensure_ascii=False,indent=2))
