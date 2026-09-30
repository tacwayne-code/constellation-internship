import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('windows_entry', Path(__file__).resolve().parents[1] / 'scripts/windows/entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


@pytest.fixture
def configured_root(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web/physical.html').write_text('test', encoding='utf-8')
    monkeypatch.setattr(entry, 'ROOT', tmp_path)
    return tmp_path


def config(**changes):
    return dict(dict(profile='PHYSICAL_CONTROL', enabled=True, control_enabled=True,
                     host='192.168.0.100', listen_host='192.168.1.88', port=102,
                     web_port=8765, rack=0, slot=1), **changes)


@pytest.mark.parametrize('changes', [dict(profile='SOFT_PLC'), dict(listen_host='0.0.0.0'),
    dict(host='255.255.255.255'), dict(host='224.1.1.1'), dict(web_port=0), dict(web_port=65536),
    dict(port='102'), dict(rack=-1), dict(slot=32), dict(control_enabled='true'), dict(enabled=False)])
def test_packaged_launcher_rejects_invalid_configuration(configured_root, changes):
    (configured_root / 'config/plc.json').write_text(json.dumps(config(**changes)), encoding='utf-8')
    with pytest.raises(ValueError):
        entry.read_config()


def test_packaged_launcher_uses_destination_network_and_port(configured_root):
    expected = config(listen_host='192.168.1.88', web_port=8888)
    (configured_root / 'config/plc.json').write_text(json.dumps(expected), encoding='utf-8-sig')
    assert entry.read_config() == expected


def test_audit_validation_rejects_unrelated_database(tmp_path):
    import sqlite3
    path = tmp_path / 'other.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE other (id INTEGER)')
    with pytest.raises(ValueError, match='not a physical PLC'):
        entry.check_audit(path)
