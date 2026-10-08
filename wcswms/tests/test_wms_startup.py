import importlib.util
import json
from pathlib import Path
import socket
import sqlite3

import pytest

import run_wms
from backend import wms_app
from backend.wms_startup import endpoint, preflight

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('doctor_wms', ROOT / 'scripts/doctor-wms.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


@pytest.fixture
def installation(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'web/dist').mkdir(parents=True)
    (tmp_path / 'web/dist/wms.html').write_text('WMS', encoding='utf-8')
    config = json.loads((ROOT / 'config/wms.example.json').read_text(encoding='utf-8'))
    config['listen_host'] = '127.0.0.1'
    (tmp_path / 'config/wms.json').write_text(json.dumps(config), encoding='utf-8')
    return tmp_path, config


@pytest.mark.parametrize('change', [dict(listen_host='0.0.0.0'), dict(listen_host='bad'),
    dict(listen_host='224.0.0.1'), dict(web_port=80), dict(web_port=65536),
    dict(web_port=True), dict(web_port='8770'), dict(control_enabled='true'), dict(profile='SOFT_PLC')])
def test_invalid_settings_rejected_before_runtime_data(installation, change):
    root, config = installation
    with pytest.raises(ValueError):
        preflight(root, {**config, **change})
    assert not (root / 'data').exists()


def test_non_object_config_is_reported():
    with pytest.raises(ValueError, match='JSON object'):
        endpoint([])


def test_occupied_port_is_distinct_from_old_listen_address(installation):
    root, config = installation
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        config['web_port'] = listener.getsockname()[1]
        with pytest.raises(ValueError, match='web port is unavailable'):
            preflight(root, config)
    assert not (root / 'data').exists()


def test_stale_migrated_address_exits_without_opening_ledger(installation, monkeypatch):
    root, config = installation
    config['listen_host'] = '192.0.2.50'
    (root / 'config/wms.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(run_wms, 'ROOT', root)
    def fail_bind(*args):
        raise OSError('not a local interface')
    monkeypatch.setattr(socket.socket, 'bind', fail_bind)
    def no_app(*args, **kwargs):
        pytest.fail('Startup must fail before opening a WMS ledger or PLC reader')
    monkeypatch.setattr(wms_app, 'create_app', no_app)
    with pytest.raises(SystemExit, match='cannot bind on this computer'):
        run_wms.main()
    assert not (root / 'data').exists()


def test_doctor_new_install_creates_no_database_and_needs_no_cad(installation):
    root, _ = installation
    before = (root / 'config/wms.json').read_bytes()
    report = doctor.collect(root)
    assert report['deployment_ready']
    assert report['inventory'] is None
    assert report['plc_connection_attempted'] is False
    assert not (root / 'data').exists()
    assert (root / 'config/wms.json').read_bytes() == before


def make_ledger(root):
    path = root / 'data/wms/warehouse.sqlite3'
    path.parent.mkdir(parents=True)
    state = dict(locations={'L-01-01-1': {}},
                 stock={'BOX': dict(status='IN_STOCK', container_type='EMPTY_BIN', quantity=0)},
                 materials={'SKU': {}}, tasks=[dict(status='COMPLETED'), dict(status='QUEUED')],
                 orders=[dict(status='COMPLETED')], allocation_policy={'side_order': 'RIGHT_FIRST'})
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY, payload TEXT)')
        db.execute('INSERT INTO ledger VALUES (1, ?)', (json.dumps(state),))
    return path


def test_doctor_requires_both_migration_databases_and_preserves_bytes(installation):
    root, _ = installation
    ledger = make_ledger(root)
    before = ledger.read_bytes()
    report = doctor.collect(root)
    assert not report['deployment_ready']
    assert report['inventory']['in_stock'] == 1
    assert report['inventory']['unfinished_tasks'] == 1
    assert not (root / 'data/physical-commands.sqlite3').exists()
    commands = root / 'data/physical-commands.sqlite3'
    with sqlite3.connect(commands) as db:
        db.execute('CREATE TABLE commands (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO commands VALUES (1)')
    commands_before = commands.read_bytes()
    report = doctor.collect(root)
    assert report['deployment_ready']
    assert report['inventory']['plc_commands'] == 1
    assert ledger.read_bytes() == before
    assert commands.read_bytes() == commands_before


def test_doctor_detects_wrong_database_without_initializing_it(installation):
    root, _ = installation
    ledger = root / 'data/wms/warehouse.sqlite3'
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b'not a SQLite database')
    report = doctor.collect(root)
    assert not report['deployment_ready']
    assert next(c for c in report['checks'] if c['name'] == 'WMS inventory')['status'] == 'missing'
    assert ledger.read_bytes() == b'not a SQLite database'


def test_doctor_rejects_command_history_without_inventory(installation):
    root, _ = installation
    commands = root / 'data/physical-commands.sqlite3'
    commands.parent.mkdir(parents=True)
    with sqlite3.connect(commands) as db:
        db.execute('CREATE TABLE commands (id INTEGER PRIMARY KEY)')
    report = doctor.collect(root)
    assert not report['deployment_ready']
    assert report['inventory'] is None
    assert not (root / 'data/wms').exists()
