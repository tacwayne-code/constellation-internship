import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('initialize_deployment', ROOT/'scripts/initialize-deployment.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


@pytest.fixture
def root(tmp_path):
    (tmp_path/'config').mkdir()
    for name in ('wms','plc'):
        (tmp_path/'config'/f'{name}.example.json').write_bytes((ROOT/'config'/f'{name}.example.json').read_bytes())
    return tmp_path


def test_fresh_install_uses_selected_addresses_and_creates_no_runtime_data(root):
    assert deployment.initialize(root,'127.0.0.1','192.168.0.123',8870) == ['wms.json','plc.json']
    config = json.loads((root/'config/wms.json').read_text(encoding='utf-8'))
    assert config['host'] == '192.168.0.123' and config['web_port'] == 8870
    assert config['listen_host'] == '127.0.0.1' and config['profile'] == 'PHYSICAL_CONTROL'
    assert config['control_enabled'] and config['auto_acknowledge']
    readonly = json.loads((root/'config/plc.json').read_text(encoding='utf-8'))
    assert readonly['control_enabled'] is False
    assert not (root/'data').exists()


def test_reinstall_never_overwrites_local_settings(root):
    deployment.initialize(root,'127.0.0.1','192.168.0.123')
    before = {name:(root/'config'/name).read_bytes() for name in ('wms.json','plc.json')}
    assert deployment.initialize(root,'127.0.0.1','192.168.0.150',8870) == []
    assert {name:(root/'config'/name).read_bytes() for name in before} == before


@pytest.mark.parametrize('host,plc,port', [('bad','192.168.0.100',8770),('0.0.0.0','192.168.0.100',8770),
                                        ('127.0.0.1','224.0.0.1',8770),('127.0.0.1','192.168.0.100',80)])
def test_invalid_configuration_leaves_no_partial_file(root,host,plc,port):
    with pytest.raises(ValueError):
        deployment.initialize(root,host,plc,port)
    assert not (root/'config/wms.json').exists()
    assert not (root/'config/plc.json').exists()
