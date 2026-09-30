"""Index actual Openness exports without treating offline data as live PLC state."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def local(tag):
    return tag.rsplit('}', 1)[-1]


def parse(path):
    root = ET.parse(path).getroot()
    for node in root.iter():
        node.tag = local(node.tag)
    return root


def index(folder, output):
    report = parse(folder / 'engineering-report.xml')
    blocks, tags, db2 = [], [], []
    for entry in report.findall('.//Block'):
        path = folder / entry.attrib['file']
        root = parse(path)
        block = next(n for n in root if n.tag.startswith('SW.Blocks.'))
        attrs = block.find('AttributeList')
        item = {'name': attrs.findtext('Name'), 'number': attrs.findtext('Number'),
                'type': block.tag, 'language': attrs.findtext('ProgrammingLanguage'),
                'memory_layout': attrs.findtext('MemoryLayout'),
                'source': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'calls': sorted({n.attrib['Name'] for n in root.iter('CallInfo')})}
        blocks.append(item)
        if item['name'] == '交互' and item['number'] == '2':
            if item['memory_layout'] != 'Standard':
                raise ValueError('DB2 layout is optimized; absolute offsets cannot be inferred')
            offset = 0
            def members(parent, prefix=''):
                nonlocal offset
                for member in parent.findall('Member'):
                    name = prefix + member.attrib['Name']
                    kind = member.attrib['Datatype']
                    if kind == 'Struct':
                        members(member, name + '.')
                    elif kind == 'Int':
                        db2.append({'address': f'DB2.DBW{offset}', 'offset': offset,
                                    'symbol': name, 'datatype': kind,
                                    'basis': 'Standard DB; sequential Int fields; offline layout inference'})
                        offset += 2
                    else:
                        raise ValueError(f'Unsupported DB2 member type {kind}; refusing guessed offsets')
            for section in attrs.findall('Interface/Sections/Section'):
                if section.attrib.get('Name') == 'Static':
                    members(section)
    for entry in report.findall('.//TagTable'):
        for tag in parse(folder / entry.attrib['file']).iter('SW.Tags.PlcTag'):
            attrs = tag.find('AttributeList')
            tags.append({'name': attrs.findtext('Name'), 'address': attrs.findtext('LogicalAddress'),
                         'datatype': attrs.findtext('DataTypeName'), 'source': entry.attrib['file']})
    result = {'source_report': str(folder/'engineering-report.xml'), 'original_program_executed': False,
              'compile_validated': bool(report.findall('.//Compile')) and all(
                  c.attrib.get('errors') == '0' for c in report.findall('.//Compile')),
              'blocks': blocks, 'tags': tags, 'db2': db2,
              'export_errors': [{'operation': e.attrib.get('operation'), 'detail': e.text} for e in report.iter('Error')]}
    output.mkdir(parents=True, exist_ok=True)
    (output/'program-index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for name, rows in [('plc-tags',tags),('db2-layout',db2)]:
        if rows:
            with (output/f'{name}.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps({'blocks':len(blocks),'tags':len(tags),'db2_words':len(db2),'export_errors':len(result['export_errors']),'output':str(output)}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('folder',type=Path)
    parser.add_argument('--output',type=Path,default=Path('data/siemens/analysis'))
    args=parser.parse_args()
    index(args.folder,args.output)
