"""Launch the separate real-PLC WMS on the company LAN."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    from run import acquire_lock, port_available
    import uvicorn
    from backend.wms_app import create_app
    os.chdir(ROOT)
    config = json.loads((ROOT / 'config/wms.json').read_text(encoding='utf-8-sig'))
    host, port = config['listen_host'], config['web_port']
    if config['profile'] != 'PHYSICAL_CONTROL' or config['control_enabled'] is not True:
        raise SystemExit('WMS startup requires explicit PHYSICAL_CONTROL / control_enabled=true in config/wms.json.')
    if not (ROOT / 'web/dist/wms.html').exists():
        raise SystemExit('WMS frontend is missing. Build web with pnpm build first.')
    lock = acquire_lock(ROOT / 'data/wms')
    try:
        if not port_available(host, port):
            raise SystemExit(f'WMS web port is occupied: {host}:{port}. No process was stopped.')
        print(f'WMS: http://{host}:{port}/ ; real PLC={config["host"]}; writes enabled, dispatch requires operator action.', flush=True)
        uvicorn.run(create_app(config=config, config_path=ROOT / 'config/wms.json'), host=host, port=port, workers=1, access_log=False)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
