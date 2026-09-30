"""Create local deployment configs once; never replace an existing installation."""
import argparse
import ipaddress
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ipv4(value):
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ValueError('Use a host IPv4 address, not a wildcard, multicast or reserved address.')
    return str(address)


def initialize(root, listen_host, plc_host, web_port=8770):
    listen_host, plc_host = ipv4(listen_host), ipv4(plc_host)
    if not 1024 <= web_port <= 65535:
        raise ValueError('Web port must be in the range 1024..65535.')
    # Bind port zero only to verify this is an address owned by the backend host.
    with socket.socket() as probe:
        probe.bind((listen_host, 0))
    created = []
    for name in ('wms', 'plc'):
        destination = root / 'config' / f'{name}.json'
        if destination.exists():
            continue
        config = json.loads((root / 'config' / f'{name}.example.json').read_text(encoding='utf-8-sig'))
        config.update(listen_host=listen_host, host=plc_host)
        if name == 'wms':
            config['web_port'] = web_port
        try:
            with destination.open('x', encoding='utf-8') as output:
                json.dump(config, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            created.append(destination.name)
        except FileExistsError:
            pass
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen-host', required=True, help='This computer company-LAN IPv4 address')
    parser.add_argument('--plc-host', default='192.168.0.100')
    parser.add_argument('--web-port', type=int, default=8770)
    args = parser.parse_args()
    try:
        created = initialize(ROOT, args.listen_host, args.plc_host, args.web_port)
    except (OSError, ValueError) as error:
        print(f'Configuration failed: {error}', file=sys.stderr)
        return 1
    print('Created: ' + (', '.join(created) or 'none; existing local configuration retained'))
    saved = json.loads((ROOT / 'config/wms.json').read_text(encoding='utf-8-sig'))
    print(f'WMS URL: http://{saved["listen_host"]}:{saved["web_port"]}/')
    print('No PLC connection, command or service startup was performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
