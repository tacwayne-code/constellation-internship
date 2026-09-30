"""Physical commissioning service with authenticated manual control; no simulator."""
import asyncio
import copy
import json
import math
import struct
import sys
import secrets
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from snap7.client import Client
from snap7.type import Parameter
from .physical_control import Command, ManualControl
from plc.s7_physical import read_cpu_state

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]


class PhysicalReader:
    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.client = None
        self.last_success = 0
        self.axis_last_success = 0
        self.axis_baseline = None
        self.data = dict(mode='PHYSICAL_READONLY', real_plc=True, connected=False,
                         task_dispatch_enabled=False, endpoint=f'{config["host"]}:{config["port"]}',
                         sample=0, signals=[], cpu_state='Unknown', last_error=None)
        self.data.update(axes=[], axis_error=None, axis_connected=False,
                         twin=dict(mode='RELATIVE_PREVIEW', calibrated=False, relative_m=None, baseline_mm=None))
        self.history = deque(maxlen=60)

    def close(self):
        if self.client:
            try:
                self.client.disconnect()
            finally:
                self.client.destroy()
                self.client = None

    def poll(self):
        with self.lock:
            try:
                if self.client is None:
                    self.client = Client(auto_reconnect=False)
                    self.client.set_param(Parameter.RecvTimeout, 1500)
                    self.client.set_param(Parameter.SendTimeout, 1500)
                    self.client.connect(self.config['host'], self.config.get('rack', 0),
                                        self.config.get('slot', 1), tcp_port=self.config['port'])
                    info = self.client.get_cpu_info()
                    self.data['cpu_model'] = info.ModuleTypeName.decode(errors='replace') if isinstance(info.ModuleTypeName, bytes) else info.ModuleTypeName
                    self.data['module_name'] = info.ModuleName.decode(errors='replace') if isinstance(info.ModuleName, bytes) else info.ModuleName
                state = read_cpu_state(self.client)
                raw = bytes(self.client.db_read(2, 0, 8))
                values = struct.unpack('>4h', raw)
                stamp = datetime.now(timezone.utc).isoformat()
                names = ['状态', '任务ID', '异常代码', '数据交互状态']
                self.data.update(connected=True, cpu_state=state, updated_at=stamp,
                                 sample=self.data['sample'] + 1, last_error=None, raw_hex=raw.hex(' '),
                                 signals=[dict(address=f'DB2.DBW{i*2}', name=name, value=value)
                                          for i, (name, value) in enumerate(zip(names, values))])
                self.last_success = monotonic()
                self.history.appendleft(dict(time=stamp, operation='DB_READ', db=2, offset=0, size=8, hex=raw.hex(' ')))
                if self.config.get('axis_feedback') == 'DB5_CURRENT_POSITION':
                    self.poll_axes()
            except Exception as error:
                self.data.update(connected=False, axis_connected=False, cpu_state='Unknown', last_error=str(error))
                try:
                    self.close()
                except Exception:
                    self.client = None

    def poll_axes(self):
        # DB5 is Standard in the supplied TIA export. These REALs mirror the
        # three technology objects' ActualPosition; length unit 1013 is mm.
        try:
            raw = bytes(self.client.db_read(5, 22, 12))
            values = struct.unpack('>3f', raw)
            if not all(math.isfinite(value) for value in values):
                raise ValueError('DB5 contains non-finite axis feedback')
            stamp = datetime.now(timezone.utc).isoformat()
            positions = dict(zip(('x', 'y', 'z'), values))
            if self.axis_baseline is None:
                self.axis_baseline = positions.copy()
                self.data['twin']['baseline_at'] = stamp
            self.data.update(axis_connected=True, axis_error=None, axis_updated_at=stamp,
                axes=[dict(axis=axis, name=name, address=f'DB5.DBD{offset}', value=value, unit='mm')
                      for axis, name, offset, value in zip(('x', 'y', 'z'), ('行走', '升降', '货叉'), (22, 26, 30), values)])
            self.data['twin'].update(baseline_mm=self.axis_baseline.copy(),
                relative_m={axis: (value-self.axis_baseline[axis])/1000 for axis, value in positions.items()})
            self.axis_last_success = monotonic()
            self.history.appendleft(dict(time=stamp, operation='DB_READ', db=5, offset=22, size=12, hex=raw.hex(' ')))
        except Exception as error:
            # Preserve the last pose but explicitly mark it stale. A failed
            # optional axis read must not replace valid DB2 status with zeros.
            self.data.update(axis_connected=False, axis_error=str(error))
            try:
                self.close()
            except Exception:
                self.client = None

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            age = monotonic() - self.last_success if self.last_success else None
            result['age_seconds'] = round(age, 2) if age is not None else None
            result['live'] = result['connected'] and age is not None and age < 5
            axis_age = monotonic() - self.axis_last_success if self.axis_last_success else None
            result['axis_age_seconds'] = round(axis_age, 2) if axis_age is not None else None
            result['axis_live'] = bool(result['live'] and result['axis_connected'] and axis_age is not None and axis_age < 5)
            result['trace'] = list(self.history)
            return result


def create_app(config=None, reader=None):
    config_path = ROOT / 'config/plc.json' if config is None else None
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config is None else config
    reader = reader or PhysicalReader(config)
    enabled = config.get('profile') == 'PHYSICAL_CONTROL' and config.get('control_enabled') is True
    write_permitted = enabled

    def control_allowed():
        nonlocal write_permitted
        if write_permitted and config_path is not None:
            try:
                current = json.loads(config_path.read_text(encoding='utf-8-sig'))
                write_permitted = current.get('profile') == 'PHYSICAL_CONTROL' and current.get('control_enabled') is True
            except (OSError, ValueError):
                write_permitted = False
        # Revocation is one-way for this process; enabling control again requires a restart.
        return write_permitted

    def status_snapshot():
        allowed = control_allowed()
        data = reader.snapshot()
        data.update(mode='PHYSICAL_CONTROL' if allowed else 'PHYSICAL_READONLY', task_dispatch_enabled=allowed)
        return data

    reader.data.update(mode='PHYSICAL_CONTROL' if enabled else 'PHYSICAL_READONLY', task_dispatch_enabled=enabled)
    control = ManualControl(reader, ROOT / 'data/physical-commands.sqlite3') if enabled else None
    key_path = ROOT / 'data/physical-control-key.txt'
    key = None
    if enabled:
        if not key_path.exists():
            key_path.write_text(secrets.token_urlsafe(18), encoding='utf-8')
        key = key_path.read_text(encoding='utf-8').strip()

    @asynccontextmanager
    async def lifespan(app):
        async def sample():
            while True:
                await asyncio.to_thread(reader.poll)
                await asyncio.sleep(1)
        task = asyncio.create_task(sample())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Wait for any in-flight worker before closing its client.
        def close():
            with reader.lock:
                reader.close()
        await asyncio.to_thread(close)

    app = FastAPI(title='真机 PLC 调试', lifespan=lifespan)

    @app.middleware('http')
    async def read_only(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if not control_allowed():
                return JSONResponse({'detail': '真机只读模式：禁止任务、停止及其他 PLC 写入'}, status_code=403)
            if request.url.path != '/api/physical/command':
                return JSONResponse({'detail': '旧仿真控制接口已停用'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': '拒绝跨来源控制'}, status_code=403)
            if not secrets.compare_digest(request.headers.get('x-control-key', ''), key):
                return JSONResponse({'detail': '请输入后台主机上的控制密钥'}, status_code=403)
        return await call_next(request)

    @app.get('/api/health')
    def health():
        data = status_snapshot()
        return dict(status='ok', mode=data['mode'], real_plc=True, real_odoo=False,
                    plc=data, task_dispatch_enabled=data['task_dispatch_enabled'])

    @app.get('/api/state')
    @app.get('/api/native-plc')
    @app.get('/api/plc/diagnostics')
    def state():
        data = status_snapshot()
        data.update(commands=control.recent() if control else [])
        return data

    @app.post('/api/physical/command')
    def command(body: Command):
        if not control or not control_allowed():
            raise HTTPException(403, '真机只读模式：控制未启用')
        return control.execute(body)

    @app.get('/')
    def home():
        if config.get('web_view') == 'twin' and not enabled:
            html = (ROOT / 'web/dist/index.html').read_text(encoding='utf-8')
            html = html.replace('<html', '<html data-app-mode="physical-readonly"', 1)
            return HTMLResponse(html, headers={'Cache-Control': 'no-store'})
        return monitor()

    @app.get('/monitor')
    def monitor():
        html = (ROOT / 'web/physical.html').read_text(encoding='utf-8')
        html = html.replace('__CONTROL_PERMITTED__', 'true' if control_allowed() else 'false')
        return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

    if config.get('web_view') == 'twin' and not enabled:
        app.mount('/assets/structure', StaticFiles(directory=ROOT / 'assets/structure'), name='structure')
        app.mount('/static', StaticFiles(directory=ROOT / 'web/dist/static'), name='web-assets')

    return app


app = create_app()
