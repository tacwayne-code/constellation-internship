"""Launch the simulation MVP with its own ports, state, logs, and process lock."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def prepare():
    config_path = ROOT / 'config/plc.simulation.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('profile') != 'SIM_V1' or config.get('host') != '127.0.0.1' or config.get('enabled') is not True:
        raise ValueError('The simulation launcher requires SIM_V1 on 127.0.0.1.')
    for field in ('port', 'web_port'):
        if type(config.get(field)) is not int or not 1 <= config[field] <= 65535:
            raise ValueError(f'Invalid simulation {field}.')
    if not (ROOT / 'web/dist/index.html').is_file():
        raise FileNotFoundError('Build the simulation frontend first: web/dist/index.html is missing.')
    runtime_dir = ROOT / 'data/simulation'
    # Set all runtime overrides together so inherited physical/default settings cannot leak in.
    os.environ.update(WCS_PLC_CONFIG=str(config_path), WCS_PORT=str(config['web_port']),
        WCS_DB=str(runtime_dir / 'warehouse.sqlite3'), WCS_PLC_ENABLED='1', WCS_PLC_PORT=str(config['port']),
        WCS_PLC_STATE=str(runtime_dir / 'plc-state.json'), WCS_PLC_TRACE=str(runtime_dir / 'plc-trace.jsonl'))
    return config_path, runtime_dir


def main():
    config_path, runtime_dir = prepare()
    from run import main as launch
    launch(config_path=config_path, runtime_dir=runtime_dir)


if __name__ == '__main__':
    main()
