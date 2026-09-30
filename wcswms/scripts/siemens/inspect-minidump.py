"""Read exception/module metadata only; never upload crash dumps."""
import argparse
import json
import struct
import mmap
from pathlib import Path


def inspect(path):
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        return inspect_bytes(path, raw)


def inspect_bytes(path, raw):
    if raw[:4] != b'MDMP':
        raise ValueError('Not a Windows minidump')
    count, directory = struct.unpack_from('<II', raw, 8)
    streams = {}
    for i in range(count):
        kind, size, offset = struct.unpack_from('<III', raw, directory + i * 12)
        streams[kind] = (offset, size)
    result = {'file': str(path), 'streams': sorted(streams)}
    modules = []
    if 4 in streams:
        offset, _ = streams[4]
        for i in range(struct.unpack_from('<I', raw, offset)[0]):
            pos = offset + 4 + i * 108
            base, size, checksum, timestamp, name_rva = struct.unpack_from('<QIIII', raw, pos)
            length = struct.unpack_from('<I', raw, name_rva)[0]
            name = raw[name_rva+4:name_rva+4+length].decode('utf-16-le')
            modules.append({'name': name, 'base': base, 'size': size})
    if 6 in streams:
        offset, _ = streams[6]
        thread = struct.unpack_from('<I', raw, offset)[0]
        code, flags, record, address, nparams = struct.unpack_from('<IIQQI', raw, offset+8)
        module = next((m for m in modules if m['base'] <= address < m['base']+m['size']), None)
        result['exception'] = {'thread': thread, 'code': hex(code), 'flags': flags,
                               'address': hex(address), 'module': module,
                               'parameters': list(struct.unpack_from('<'+'Q'*min(nparams,15),raw,offset+40))}
        if module:
            result['exception']['module_offset'] = hex(address-module['base'])
    result['module_count'] = len(modules)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(inspect(args.path), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text)
