"""Read-only WMS deployment and inventory check; never connects to a PLC."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.wms_startup import endpoint, check_local_address


def inventory_summary(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Inventory database integrity check failed')
        row = db.execute('SELECT payload FROM ledger WHERE id=1').fetchone()
        if row is None:
            raise ValueError('WMS ledger row is missing')
        state = json.loads(row[0])
    stock = list(state['stock'].values())
    stored = [item for item in stock if item['status'] == 'IN_STOCK']
    return dict(locations=len(state['locations']), stock_records=len(stock),
                stock_status=dict(Counter(item['status'] for item in stock)),
                in_stock=len(stored), in_stock_types=dict(Counter(item['container_type'] for item in stored)),
                in_stock_material_quantity=sum(item['quantity'] for item in stored if item['container_type'] == 'MATERIAL'),
                material_catalog=len(state.get('materials', {})), tasks=len(state['tasks']),
                task_status=dict(Counter(task['status'] for task in state['tasks'])),
                unfinished_tasks=sum(task['status'] not in {'COMPLETED', 'CANCELLED'} for task in state['tasks']),
                orders=len(state.get('orders', [])),
                order_status=dict(Counter(order['status'] for order in state.get('orders', []))),
                allocation_policy=state.get('allocation_policy'))


def commands_count(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('PLC command database integrity check failed')
        return db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]


def collect(root=ROOT, live=False):
    root = Path(root).resolve()
    checks, inventory, health = [], None, None
    config = None
    def add(name, ready, detail, required=True):
        checks.append(dict(name=name, status='ready' if ready else ('missing' if required else 'pending'),
                           detail=detail, required=required))

    add('Python', sys.version_info >= (3, 12) and sys.maxsize > 2**32, platform.python_version())
    for name in ('fastapi', 'uvicorn', 'python-snap7'):
        try:
            add(name, True, importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            add(name, False, 'Run scripts/setup-wms.ps1')
    add('WMS frontend', (root / 'web/dist/wms.html').is_file(), 'web/dist/wms.html')
    try:
        config = json.loads((root / 'config/wms.json').read_text(encoding='utf-8-sig'))
        host, port = endpoint(config)
        add('WMS configuration', True, dict(listen_host=host, web_port=port, plc_host=config.get('host')))
        try:
            check_local_address(host)
            add('Local listen address', True, host)
        except ValueError as error:
            add('Local listen address', False, str(error))
    except (OSError, ValueError, TypeError) as error:
        add('WMS configuration', False, str(error))
        config = None

    ledger = root / 'data/wms/warehouse.sqlite3'
    commands = root / 'data/physical-commands.sqlite3'
    if ledger.exists():
        try:
            inventory = inventory_summary(ledger)
            add('WMS inventory', True, inventory)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
            add('WMS inventory', False, str(error))
    else:
        add('WMS inventory', False,
            'Inventory ledger is missing; restore both databases together.' if commands.exists()
            else 'No existing inventory; first production startup creates an empty ledger.',
            required=commands.exists())
    if commands.exists():
        try:
            count = commands_count(commands)
            add('PLC command history', True, dict(commands=count))
            if inventory is not None:
                inventory['plc_commands'] = count
        except (sqlite3.Error, ValueError) as error:
            add('PLC command history', False, str(error))
    else:
        add('PLC command history', False, 'Missing data/physical-commands.sqlite3; migrate with the inventory ledger.',
            required=ledger.exists())

    if live and config is not None:
        base = f'http://{host}:{port}'
        try:
            # Bypass machine HTTP proxies for this explicitly local address.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(base + '/api/health', timeout=3) as response:
                health = json.load(response)
            valid = health.get('status') == 'ok' and health.get('mode') == 'PHYSICAL_WMS'
            add('WMS live endpoint', valid, dict(url=base, version=health.get('version'), mode=health.get('mode')))
            if valid:
                add('PLC feedback reported by WMS', health.get('plc', {}).get('live') is True,
                    health.get('readiness'), required=False)
        except (OSError, ValueError) as error:
            add('WMS live endpoint', False, str(error))
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), root=str(root), checks=checks,
                deployment_ready=all(item['status'] == 'ready' for item in checks if item['required']),
                inventory=inventory, health=health, plc_connection_attempted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Read the configured WMS HTTP health endpoint only')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = collect(live=args.live)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report['checks']:
            print(f"[{check['status'].upper():7}] {check['name']}: {check['detail']}")
        print('WMS deployment ready:', report['deployment_ready'])
        print('No PLC connection, command, inventory update or service startup was performed.')
    return 0 if report['deployment_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
