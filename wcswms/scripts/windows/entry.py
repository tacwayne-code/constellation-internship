"""Standalone physical commissioning launcher; never starts a simulator."""
import argparse
import ipaddress
import json
import socket
import sqlite3
import struct
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(ROOT))


def read_config():
    config = json.loads((ROOT / 'config/plc.json').read_text(encoding='utf-8-sig'))
    if config.get('profile') not in ('PHYSICAL_CONTROL', 'PHYSICAL_READONLY'):
        raise ValueError('This package supports physical PLC profiles only.')
    for field in ('listen_host', 'host'):
        address = ipaddress.IPv4Address(config[field])
        if address.is_unspecified or address.is_multicast or address == ipaddress.IPv4Address('255.255.255.255'):
            raise ValueError(f'{field} must be a specific unicast IPv4 address.')
    for field, default, minimum, maximum in [('web_port', 8765, 1, 65535), ('port', 102, 1, 65535),
                                            ('rack', 0, 0, 7), ('slot', 1, 0, 31)]:
        value = config.get(field, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'Invalid {field}.')
    if config.get('enabled') is not True or type(config.get('control_enabled')) is not bool:
        raise ValueError('Explicit enabled/control_enabled settings are required.')
    if not (ROOT / 'web/physical.html').is_file():
        raise ValueError('Missing web/physical.html.')
    return config


def check_audit(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Task history integrity check failed.')
        fields = {row[1] for row in db.execute('PRAGMA table_info(commands)')}
        if not {'id', 'payload', 'result', 'task_id'}.issubset(fields):
            raise ValueError('This is not a physical PLC task history database.')
        return db.execute('SELECT count(*) FROM commands').fetchone()[0]


def self_test():
    import fastapi
    import snap7
    import uvicorn
    from backend.physical_control import Command
    from plc.s7_physical import read_cpu_state
    from types import SimpleNamespace
    zeros = {f'{prefix}_{field}': 0 for prefix in ('source', 'target')
             for field in ('side', 'level', 'column', 'depth')}
    command = Command(request_id='package-self-test', action='task', function=4, dock=0, **zeros)
    assert command.dock == 0 and command.target_level == 0
    fake_client = SimpleNamespace(read_szl=lambda *args: SimpleNamespace(
        Header=SimpleNamespace(LengthDR=8), Data=bytes.fromhex('000400015102ff08')))
    assert read_cpu_state(fake_client) == 'S7CpuStatusRun'
    with sqlite3.connect(':memory:') as db:
        assert db.execute('SELECT 1').fetchone()[0] == 1
    assert struct.unpack('>h', b'\0\0') == (0,)
    assert 'dock_zero' in (ROOT / 'web/physical.html').read_text(encoding='utf-8')
    print(json.dumps(dict(status='ok', python=sys.version.split()[0], fastapi=fastapi.__version__,
                         snap7=snap7.__version__, uvicorn=uvicorn.__version__, plc_writes=0)))


def smoke_test():
    """Test bundled HTTP imports and assets with a fake reader and loopback listener."""
    import uvicorn
    from backend.physical_app import create_app
    class OfflineReader:
        def __init__(self):
            self.data = {}
            self.lock = threading.RLock()
        def poll(self): pass
        def close(self): pass
        def snapshot(self):
            return dict(self.data, live=False, connected=False, signals=[], trace=[], endpoint='TEST ONLY')
    app = create_app({'host': '127.0.0.1', 'port': 1, 'control_enabled': False}, OfflineReader())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(16)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, loop='asyncio', http='h11'))
        worker = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                if not worker.is_alive():
                    raise RuntimeError('Bundled HTTP server failed to start.')
                time.sleep(.05)
            else:
                raise RuntimeError('Bundled HTTP server startup timed out.')
            base = f'http://127.0.0.1:{port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(base + '/api/health', timeout=5) as response:
                health = json.load(response)
            assert health['status'] == 'ok' and health['task_dispatch_enabled'] is False
            with opener.open(base, timeout=5) as response:
                assert 'dock_zero' in response.read().decode('utf-8')
            print(json.dumps(dict(status='ok', http=True, assets=True, plc_connections=0, plc_writes=0)))
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError('Bundled HTTP test server did not exit.')


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--self-test', action='store_true')
    modes.add_argument('--smoke-test', action='store_true')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--audit-check', type=Path)
    modes.add_argument('--audit-copy', type=Path)
    parser.add_argument('--audit-destination', type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.audit_check:
        print(json.dumps(dict(status='ok', records=check_audit(args.audit_check))))
    elif args.audit_copy:
        if args.audit_destination is None or args.audit_destination.exists():
            raise ValueError('Specify a new --audit-destination file.')
        check_audit(args.audit_copy)
        with sqlite3.connect(args.audit_copy.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(args.audit_destination) as target:
                source.backup(target)
        print(json.dumps(dict(status='ok', records=check_audit(args.audit_destination))))
    elif args.smoke_test:
        smoke_test()
    else:
        config = read_config()
        if args.check:
            # Verify the configured web address belongs to this machine; no PLC connection.
            with socket.socket() as sock:
                sock.bind((config['listen_host'], 0))
            print(json.dumps(dict(status='ok', url=f'http://{config["listen_host"]}:{config.get("web_port", 8765)}/',
                                 plc_connections=0, plc_writes=0)))
        else:
            print(f'Control key file: {ROOT / "data/physical-control-key.txt"}', flush=True)
            from run import main as run_backend
            run_backend()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
