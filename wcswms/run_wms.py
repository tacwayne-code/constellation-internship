"""Launch the separate real-PLC WMS on the company LAN."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    from run import acquire_lock
    from backend.wms_startup import preflight
    import uvicorn
    from backend.wms_app import create_app
    os.chdir(ROOT)
    try:
        config = json.loads((ROOT / 'config/wms.json').read_text(encoding='utf-8-sig'))
        host, port = preflight(ROOT, config)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
    lock = acquire_lock(ROOT / 'data/wms')
    try:
        print(f'WMS: http://{host}:{port}/ ; real PLC={config["host"]}; writes enabled, dispatch requires operator action.', flush=True)
        uvicorn.run(create_app(config=config, config_path=ROOT / 'config/wms.json'), host=host, port=port, workers=1, access_log=False)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
