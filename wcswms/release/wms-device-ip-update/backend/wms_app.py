"""Touch WMS with operator-dispatched tasks and correlated automatic receipts."""
import asyncio
import csv
import io
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .wms_service import WmsReader, WmsService, feedback
from .wms_store import WmsStore, WmsError, ACTIVE, rack_layouts, location_token, display_location
from .wms_allocation import allocation_policy
from .wms_device import DeviceSettings

ROOT = Path(__file__).resolve().parents[1]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Identified(Input):
    request_id: str = Field(min_length=8, max_length=100)


class MaterialInput(Identified):
    material_name: str = Field(default='', max_length=120)
    model: str = Field(default='', max_length=120)
    specification: str = Field(default='', max_length=200)


class MaterialUpdate(MaterialInput):
    barcode: str = Field(min_length=1, max_length=64)
    expected_updated_at: str = Field(min_length=1, max_length=60)


class Goods(MaterialInput):
    container_type: Literal['MATERIAL', 'EMPTY_BIN'] = 'MATERIAL'
    barcode: str = Field(min_length=1, max_length=64, pattern=r'^[^\s\x00-\x1f\x7f]+$')
    sku: str = Field(default='', max_length=80)
    quantity: int = Field(default=1, ge=0, le=1000000, strict=True)
    batch: str = Field(default='', max_length=80)


class TaskInput(Goods):
    kind: Literal['INBOUND', 'OUTBOUND']
    location: str = Field(default='AUTO', max_length=64)
    depth: Literal[0, 1, 2] = 0


class TaskBatchInput(Identified):
    tasks: list[TaskInput] = Field(min_length=1, max_length=50)


class OrderInput(MaterialInput):
    kind: Literal['INBOUND', 'OUTBOUND']
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=1000000, strict=True)
    batch: str = Field(default='', max_length=80)
    box_mode: Literal['NEW', 'EXISTING'] = 'NEW'
    box_barcode: str = Field(default='', max_length=64)


class OrderBatchInput(Identified):
    orders: list[OrderInput] = Field(min_length=1, max_length=50)


class ReturnInput(Identified):
    scanned_barcode: str = Field(default='', max_length=64)
    scanned_sku: str = Field(default='', max_length=80)
    confirmed: bool = False


class LocationInput(Identified):
    side: int = Field(ge=1, le=2, strict=True)
    level: int = Field(ge=1, le=99, strict=True)
    column: int = Field(ge=1, le=99, strict=True)
    depth: Literal[1, 2] | None = None
    verified_empty: bool = False


class Confirm(Input):
    confirmed: bool = False


class AllocationInput(Input):
    priority: Literal['LEVEL_COLUMN', 'COLUMN_LEVEL', 'SIDE_LEVEL_COLUMN', 'SIDE_COLUMN_LEVEL', 'LEVEL_SIDE_COLUMN', 'LEVEL_COLUMN_SIDE', 'COLUMN_SIDE_LEVEL', 'COLUMN_LEVEL_SIDE']
    side_order: Literal['LEFT_FIRST', 'RIGHT_FIRST', 'BALANCED']
    level_order: Literal['LOW_FIRST', 'HIGH_FIRST']
    column_order: Literal['FRONT_FIRST', 'BACK_FIRST']


class AllocationSettingsInput(Identified):
    allocation: AllocationInput
    expected_revision: int = Field(ge=0, strict=True)


class RackLayoutInput(Identified):
    side: int = Field(ge=1, le=2, strict=True)
    levels: int = Field(ge=1, le=99, strict=True)
    columns: int = Field(ge=1, le=99, strict=True)
    depths: Literal[1, 2] = 1
    expected_revision: int = Field(ge=0, strict=True)
    verified_empty: bool = False
    allocation: AllocationInput | None = None
    expected_allocation_revision: int | None = Field(default=None, ge=0, strict=True)


class LocationStateInput(Identified):
    action: Literal['DISABLE', 'ENABLE', 'RELEASE']
    expected_token: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]+$')
    note: str = Field(min_length=2, max_length=200)
    confirmed: bool = False


class SiteReady(Input):
    site_ready: bool = False


class StocktakeInput(Goods):
    location: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=2, max_length=200)
    confirmed: bool = False


class HandoverInput(Identified):
    barcode: str = Field(min_length=1, max_length=64)
    confirmed: bool = False


class StopInput(Identified):
    site_ready: bool = False


class ResolveInput(SiteReady):
    outcome: Literal['AT_SOURCE', 'AT_DESTINATION']
    note: str = Field(min_length=4, max_length=200)


class ExternalAck(SiteReady):
    expected_task_id: int = Field(ge=0, le=32767)
    expected_status: Literal[1, 2, 3, 4, 5, 6, 8]


class DeviceSettingsInput(Input):
    host: str = Field(min_length=7, max_length=15)
    expected_revision: int = Field(ge=0, strict=True)


def create_app(config=None, data_dir=None, reader=None, key=None, commands_path=None, automatic=True, config_path=None):
    if config is None:
        config_path = Path(config_path or ROOT / 'config/wms.json')
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    data_dir = Path(data_dir or ROOT / 'data/wms')
    data_dir.mkdir(parents=True, exist_ok=True)
    store = WmsStore(data_dir / 'warehouse.sqlite3', config)
    reader = reader or WmsReader(config)
    enabled = config.get('profile') == 'PHYSICAL_CONTROL' and config.get('control_enabled') is True
    service = WmsService(store, reader, commands_path or ROOT / 'data/physical-commands.sqlite3', enabled=enabled,
                         auto_acknowledge=config.get('auto_acknowledge', True) is True)
    device = DeviceSettings(config, config_path, service)
    if key is None:
        key_path = ROOT / config['control_key_path']
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_text(secrets.token_urlsafe(24), encoding='utf-8')
        key = key_path.read_text(encoding='utf-8').strip()
    if not key:
        raise RuntimeError('Control key cannot be empty')

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        async def sample():
            while True:
                try:
                    await asyncio.to_thread(service.poll)
                except Exception:
                    import logging
                    logging.exception('WMS feedback reconciliation failed; no command retry')
                await asyncio.sleep(1)
        worker = asyncio.create_task(sample()) if automatic else None
        yield
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        def close():
            with service.lock, reader.lock:
                reader.close()
        await asyncio.to_thread(close)

    app = FastAPI(title='常规仓储 WMS', version='1.6.0', lifespan=lifespan)
    app.state.store, app.state.service = store, service
    app.state.device = device

    @app.middleware('http')
    async def operator_access(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': '拒绝跨来源操作'}, status_code=403)
            supplied = request.headers.get('x-control-key', '')
            if not secrets.compare_digest(supplied.encode(), key.encode()):
                return JSONResponse({'detail': '请先输入后台主机上的控制密钥，解锁操作'}, status_code=401)
        response = await call_next(request)
        if request.url.path.startswith('/api/') or request.url.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(WmsError)
    async def domain_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.get('/api/state')
    def state():
        ledger = store.read()
        stock = list(ledger['stock'].values())
        stock_by_location = {item['location']: item for item in stock if item['status'] == 'IN_STOCK'}
        tasks_by_location = {}
        for task in ledger['tasks']:
            if task['status'] in ACTIVE:
                tasks_by_location.setdefault(task['location'], []).append(task)
        locations = []
        for location in ledger['locations'].values():
            item = stock_by_location.get(location['id'])
            status = 'DISABLED' if not location['enabled'] else 'RESERVED' if location['reserved'] else 'OCCUPIED' if item else 'EMPTY' if location['verified'] else 'UNVERIFIED'
            inbound_access_error = store.access_error(ledger, location, inbound=True)
            if status == 'EMPTY' and inbound_access_error:
                status = 'BLOCKED'
            related = tasks_by_location.get(location['id'], [])
            locations.append({**location, 'barcode': item['barcode'] if item else None, 'status': status,
                'display_id': display_location(location['id']),
                'access_error': store.access_error(ledger, location),
                'inbound_access_error': inbound_access_error,
                'management_token': location_token(location, item, related),
                'blocking_tasks': [task['number'] for task in related]})
        return dict(mode='PHYSICAL_WMS', real_plc=True, write_enabled=enabled,
                    device_settings=device.snapshot(),
                    auto_acknowledge=service.auto_acknowledge, plc=reader.snapshot(),
                    readiness=service.readiness(), locations=locations, rack_layouts=rack_layouts(ledger), allocation_policy=allocation_policy(ledger), stock=stock,
                    tasks=[task for index, task in enumerate(ledger['tasks']) if index < 200 or task['status'] in ACTIVE], events=ledger['events'][:80],
                    orders=[order for index, order in enumerate(ledger.get('orders', [])) if index < 200 or order['status'] not in {'COMPLETED', 'CANCELLED'}],
                    docks=dict(inbound=config['inbound_dock'], outbound=config['outbound_dock']))

    @app.get('/api/health')
    def health():
        return dict(status='ok', version='1.6.0', features=['empty_bins', 'batch_tasks', 'double_depth', 'auto_acknowledge', 'material_return_orders', 'rack_allocation_policy', 'balanced_allocation', 'device_connection_settings'], mode='PHYSICAL_WMS', real_plc=True, write_enabled=enabled,
                    auto_acknowledge=service.auto_acknowledge,
                    plc=reader.snapshot(), readiness=service.readiness())

    @app.post('/api/device/settings')
    def device_settings(body: DeviceSettingsInput):
        return device.change_host(body.host, body.expected_revision)

    @app.get('/api/lookup')
    def lookup(barcode: str):
        barcode = barcode.strip()
        ledger = store.read()
        item = ledger['stock'].get(barcode)
        material = ledger['materials'].get(item['sku'].strip() if item else barcode)
        return dict(barcode=barcode, stock=item, material=material,
                    task=next((task for task in ledger['tasks'] if task['barcode'] == barcode and task['status'] in ACTIVE), None))

    @app.get('/api/materials/lookup')
    def lookup_material(sku: str = Query(min_length=1, max_length=80)):
        sku = sku.strip()
        if not sku:
            raise WmsError('请填写物料编码')
        return dict(sku=sku, material=store.read()['materials'].get(sku))

    @app.post('/api/auth/check')
    def auth_check():
        return dict(ok=True, write_enabled=enabled)

    @app.post('/api/tasks', status_code=201)
    def create(body: TaskInput):
        return store.create_task(body.model_dump())

    @app.post('/api/tasks/batch', status_code=201)
    def create_batch(body: TaskBatchInput):
        return store.create_tasks(body.model_dump())

    @app.get('/api/orders/options')
    def order_options(sku: str = Query(min_length=1, max_length=80)):
        return store.orders.options(sku.strip())

    @app.post('/api/orders', status_code=201)
    def create_order(body: OrderInput):
        return store.orders.create(body.model_dump())

    @app.post('/api/orders/batch', status_code=201)
    def create_order_batch(body: OrderBatchInput):
        return store.orders.create_batch(body.model_dump())

    @app.post('/api/orders/{ident}/return')
    def return_order(ident: str, body: ReturnInput):
        return service.return_order(ident, body.model_dump())

    @app.post('/api/tasks/{ident}/cancel')
    def cancel(ident: str):
        with service.lock:
            return store.cancel(ident)

    @app.post('/api/tasks/{ident}/dispatch')
    def dispatch(ident: str, body: SiteReady):
        return service.dispatch(ident, body.site_ready)

    @app.post('/api/tasks/{ident}/acknowledge')
    def acknowledge(ident: str, body: SiteReady):
        return service.acknowledge(ident, body.site_ready)

    @app.post('/api/tasks/{ident}/resolve')
    def resolve(ident: str, body: ResolveInput):
        return service.resolve(ident, body.outcome, body.note, body.site_ready)

    @app.post('/api/locations', status_code=201)
    def add_location(body: LocationInput):
        return store.add_location(body.model_dump())

    @app.post('/api/locations/layout')
    def configure_rack(body: RackLayoutInput):
        return store.configure_rack(body.model_dump())

    @app.post('/api/allocation-policy')
    def configure_allocation(body: AllocationSettingsInput):
        return store.configure_allocation(body.model_dump())

    @app.post('/api/locations/{ident}/verify-empty')
    def verify_empty(ident: str, body: Confirm):
        if not body.confirmed:
            raise WmsError('请现场核对该库位确实为空')
        return store.verify_empty(ident)

    @app.post('/api/locations/{ident}/state')
    def location_state(ident: str, body: LocationStateInput):
        with service.lock:
            return store.manage_location(ident, body.model_dump())

    @app.post('/api/stocktake')
    def stocktake(body: StocktakeInput):
        if not body.confirmed:
            raise WmsError('请确认现场盘点结果')
        return store.stocktake(body.model_dump())

    @app.post('/api/handover')
    def handover(body: HandoverInput):
        if not body.confirmed:
            raise WmsError('请确认扫码料箱已从出口取走')
        return store.handover(body.model_dump())

    @app.post('/api/inventory/material')
    def update_material(body: MaterialUpdate):
        return store.update_material(body.model_dump())

    @app.post('/api/device/stop')
    def stop(body: StopInput):
        return service.stop(body.request_id, body.site_ready)

    @app.post('/api/device/acknowledge')
    def external_ack(body: ExternalAck):
        if not body.site_ready or not enabled:
            raise WmsError('请确认外部任务已处理及现场控制权')
        with service.lock, reader.lock:
            if any(task['status'] in {'DISPATCHING', 'SENT', 'RUNNING', 'REVIEW', 'PLC_DONE'} for task in store.read()['tasks']):
                raise WmsError('存在本 WMS 的未结束任务，请从对应任务处理')
            reader.poll()
            snap = reader.snapshot()
            if feedback(snap).get('DB2.DBW0') != body.expected_status or snap.get('command_task_id') != body.expected_task_id:
                raise WmsError('PLC 任务状态已改变，请刷新后核对')
            store.mutate(lambda ledger: store.event(ledger, f'请求确认外部完成回执，PLC 任务号 {body.expected_task_id}'))
            service._ack_plc(body.expected_task_id, body.expected_status)
            store.mutate(lambda ledger: store.event(ledger, '外部完成回执已确认；未变更 WMS 库存'))
            return dict(ok=True)

    @app.get('/api/inventory.csv')
    def inventory_csv():
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['料箱条码', '物料编码', '物料名称', '型号', '规格', '数量', '批次', '库位', '状态', '料箱类型', '伸位'])
        def safe(value):
            text = str(value)
            return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
        ledger = store.read()
        for item in ledger['stock'].values():
            loc = ledger['locations'].get(item['location'])
            values = {**item, 'location': display_location(item['location'])}
            writer.writerow([safe(values[key]) for key in ('barcode', 'sku', 'material_name', 'model', 'specification', 'quantity', 'batch', 'location', 'status')]
                + ['空箱' if item['container_type'] == 'EMPTY_BIN' else '物料箱', ('双伸' if loc['depth'] == 2 else '单伸') if loc else ''])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=inventory.csv'})

    @app.get('/')
    def home():
        return HTMLResponse((ROOT / 'web/dist/wms.html').read_text(encoding='utf-8'))

    app.mount('/static', StaticFiles(directory=ROOT / 'web/dist/static'), name='static')
    return app
