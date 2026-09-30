"""Start the configured physical PLC monitor only if every control setting is off."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    path = ROOT / 'config/plc.json'
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('profile') != 'PHYSICAL_READONLY' or config.get('control_enabled') is not False:
        raise SystemExit('Read-only startup refused: set PHYSICAL_READONLY and control_enabled=false in config/plc.json.')
    from run import main as launch
    launch(config_path=path)


if __name__ == '__main__':
    main()
