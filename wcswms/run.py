"""Start the local WCS and its owned S7 protocol simulator as one lab session."""
import json
import os
import socket
import signal
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from snap7.client import Client
from snap7.type import Parameter

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def port_available(host, port):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def acquire_lock(runtime_dir=None):
    runtime_dir = Path(runtime_dir) if runtime_dir is not None else ROOT / 'data'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    handle = open(runtime_dir / 'server.lock', 'a+b')
    handle.seek(0)
    handle.write(b'0')
    handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit('This backend is already running. Open its configured web address.')
    return handle


def main(config_path=None, runtime_dir=None):
    os.chdir(ROOT)
    config_path = Path(config_path) if config_path is not None else ROOT / 'config/plc.json'
    runtime_dir = Path(runtime_dir) if runtime_dir is not None else ROOT / 'data'
    os.environ['WCS_PLC_CONFIG'] = str(config_path.resolve())
    lock = acquire_lock(runtime_dir)
    child = None
    log = None
    try:
        config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {'enabled': False}
        web_port = int(os.environ.get('WCS_PORT', str(config.get('web_port', 8765))))
        physical = config.get('profile') in ('PHYSICAL_READONLY', 'PHYSICAL_CONTROL')
        web_host = config.get('listen_host', '127.0.0.1') if physical else '127.0.0.1'
        if not port_available(web_host, web_port):
            raise SystemExit(f'Web port {web_port} is occupied; no process was stopped.')
        if physical:
            print(f'Physical PLC commissioning: {config["host"]}:{config["port"]}; manual control={config.get("control_enabled", False)}; web=http://{web_host}:{web_port}', flush=True)
            uvicorn.run('backend.physical_app:app', host=web_host, port=web_port, workers=1, access_log=False)
            return
        if config.get('enabled', True) and os.environ.get('WCS_PLC_ENABLED', '1') != '0':
            host = config.get('host', '127.0.0.1')
            if host != '127.0.0.1':
                raise SystemExit('This lab launcher accepts only 127.0.0.1; real PLC access is disabled.')
            port = int(os.environ.get('WCS_PLC_PORT', str(config.get('port', 1102))))
            state = os.environ.get('WCS_PLC_STATE', config.get('state_file', 'data/plc-state.json'))
            trace = os.environ.get('WCS_PLC_TRACE', config.get('trace_file', 'data/plc-trace.jsonl'))
            if not port_available(host, port):
                raise SystemExit(f'PLC port {port} is occupied. Refusing to attach to an unknown PLC process.')
            log = open(runtime_dir / 'plc-runtime.log', 'a', encoding='utf-8', buffering=1)
            command = [sys.executable, '-m', 'plc.runtime', '--host', host, '--port', str(port), '--state', state, '--trace', trace, '--cycle-ms', str(config.get('scan_interval_ms', 50)), '--lease-ms', str(config.get('heartbeat_timeout_ms', 2500))]
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
                env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'},
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0)
            deadline = time.monotonic() + 15
            while True:
                if child.poll() is not None:
                    raise SystemExit(f'PLC runtime failed to start. See {runtime_dir / "plc-runtime.log"} .')
                try:
                    probe = Client(auto_reconnect=False)
                    probe.set_param(Parameter.RecvTimeout, 500)
                    try:
                        probe.connect(host, 0, 1, tcp_port=port)
                        probe.db_read(100, 0, 4)
                    finally:
                        probe.disconnect()
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise SystemExit(f'PLC startup timed out. See {runtime_dir / "plc-runtime.log"} .')
                    time.sleep(.1)
            print(f'Local S7 protocol PLC PID={child.pid}, endpoint={host}:{port}', flush=True)
            print('This executes plc/runtime.py, NOT the original Siemens .ap18 project.', flush=True)
        print(f'Warehouse lab: http://127.0.0.1:{web_port}', flush=True)
        uvicorn.run('backend.app:app', host='127.0.0.1', port=web_port, workers=1, access_log=False)
    finally:
        if child is not None and child.poll() is None:
            if os.name == 'nt':
                try:
                    child.send_signal(signal.CTRL_BREAK_EVENT)
                    child.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    # The venv launcher can own a second Python process. Close only
                    # this launcher's owned tree, never all Python/PLC processes.
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    child.wait(timeout=5)
            else:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        if log is not None:
            log.close()
        lock.close()


if __name__ == '__main__':
    main()



