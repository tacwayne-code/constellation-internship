import json
import os

import pytest
import run_simulation


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'web/dist').mkdir(parents=True)
    (tmp_path / 'web/dist/index.html').write_text('simulation')
    monkeypatch.setattr(run_simulation, 'ROOT', tmp_path)
    for key in ('WCS_PLC_CONFIG', 'WCS_PORT', 'WCS_DB', 'WCS_PLC_ENABLED', 'WCS_PLC_PORT', 'WCS_PLC_STATE', 'WCS_PLC_TRACE'):
        monkeypatch.setenv(key, 'old-environment-value')
    return tmp_path


def configure(root, **overrides):
    config = dict(profile='SIM_V1', host='127.0.0.1', enabled=True, port=1102, web_port=8766)
    config.update(overrides)
    (root / 'config/plc.simulation.json').write_text(json.dumps(config), encoding='utf-8')


def test_simulation_overrides_state_and_leaves_physical_config_unchanged(workspace):
    physical = workspace / 'config/plc.json'
    physical.write_text('{"profile":"PHYSICAL_CONTROL","host":"192.168.0.100"}', encoding='utf-8')
    before = physical.read_bytes()
    configure(workspace)
    path, runtime = run_simulation.prepare()
    assert path == workspace / 'config/plc.simulation.json'
    assert runtime == workspace / 'data/simulation'
    assert os.environ['WCS_DB'] == str(runtime / 'warehouse.sqlite3')
    assert os.environ['WCS_PLC_STATE'] == str(runtime / 'plc-state.json')
    assert os.environ['WCS_PLC_TRACE'] == str(runtime / 'plc-trace.jsonl')
    assert os.environ['WCS_PORT'] == '8766'
    assert os.environ['WCS_PLC_PORT'] == '1102'
    assert os.environ['WCS_PLC_ENABLED'] == '1'
    assert physical.read_bytes() == before


@pytest.mark.parametrize('overrides', [dict(profile='PHYSICAL_CONTROL'), dict(host='192.168.0.100'),
    dict(host='0.0.0.0'), dict(enabled=False), dict(port=0), dict(web_port=65536)])
def test_simulation_rejects_wrong_mode_or_endpoint(workspace, overrides):
    configure(workspace, **overrides)
    with pytest.raises(ValueError):
        run_simulation.prepare()
    assert os.environ['WCS_PLC_CONFIG'] == 'old-environment-value'


def test_separate_runtime_locks_can_coexist(tmp_path):
    from run import acquire_lock
    physical = acquire_lock(tmp_path / 'physical')
    simulation = acquire_lock(tmp_path / 'simulation')
    try:
        with pytest.raises(SystemExit, match='already running'):
            acquire_lock(tmp_path / 'simulation')
    finally:
        simulation.close()
        physical.close()
