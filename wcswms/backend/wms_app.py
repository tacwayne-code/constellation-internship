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
from .wms_store import WmsStore, WmsError, ACTIVE, rack_layouts, location_token, display_location, task_locations
from .wms_allocation import allocation_policy
from .wms_device import DeviceSettings
from .wms_auth import Users, AuthError, PERMISSIONS, ROLES, DEFAULTS, COOKIE, SESSION_SECONDS
from . import wms_material_import
from . import wms_scan_policy
from . import wms_transfer
from . import wms_aging

ROOT = Path(__file__).resolve().parents[1]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Identified(Input):
    request_id: str = Field(min_length=8, max_length=100)


class ScanPolicyInput(Identified):
    require_barcode: bool = Field(strict=True)
    require_sku: bool = Field(strict=True)
    expected_revision: int = Field(ge=0)


class TransferInput(Identified):
    barcode: str = Field(min_length=1, max_length=64)
    source_location: str = Field(min_length=1, max_length=64)
    target_location: str = Field(min_length=1, max_length=64)
    expected_updated_at: str = Field(min_length=1, max_length=60)


class AgingStartInput(Identified):
    expected_token: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]+$')
    limit_cycles: int = Field(default=0, ge=0, le=1000000, strict=True)
    step_timeout: int = Field(default=300, ge=30, le=3600, strict=True)
    site_ready: bool = Field(default=False, strict=True)


class AgingStopInput(Identified):
    run_id: str = Field(min_length=32, max_length=32, pattern=r'^[0-9a-f]+$')


class MaterialInput(Identified):
    material_name: str = Field(default='', max_length=120)
    model: str = Field(default='', max_length=120)
    specification: str = Field(default='', max_length=200)


class MaterialUpdate(MaterialInput):
    barcode: str = Field(min_length=1, max_length=64)
    sku: str = Field(default='', max_length=80)
    expected_updated_at: str = Field(min_length=1, max_length=60)


class MaterialFile(Input):
    filename: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2800000)


class MaterialImport(MaterialFile, Identified):
    expected_token: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]+$')
    confirmed: bool = False


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
    scanned_skus: list[str] = Field(default_factory=list, max_length=50)
    expected_scan_revision: int | None = Field(default=None, ge=0)
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


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=40)
    role: Literal['admin', 'operator', 'viewer', 'custom'] = 'operator'
    permissions: list[str] = Field(default_factory=list, max_length=6)


class BootstrapInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=40)
    control_key: str = Field(min_length=1, max_length=256)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=40)
    role: Literal['admin', 'operator', 'viewer', 'custom']
    permissions: list[str] = Field(default_factory=list, max_length=6)
    enabled: bool = Field(strict=True)
    expected_revision: int = Field(ge=1, strict=True)
    password: str = Field(default='', max_length=128)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class WarehouseInput(Input):
    name: str = Field(min_length=1, max_length=40)
    note: str = Field(default='', max_length=200)


class WarehouseUpdate(WarehouseInput):
    expected_revision: int = Field(ge=1, strict=True)


class SiteDeviceInput(Input):
    warehouse_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=40)
    host: str = Field(min_length=7, max_length=15)
    port: int = Field(default=102, ge=1, le=65535, strict=True)
    rack: int = Field(default=0, ge=0, le=7, strict=True)
    slot: int = Field(default=1, ge=0, le=31, strict=True)
    inbound_dock: int = Field(default=1, ge=1, le=32767, strict=True)
    outbound_dock: int = Field(default=1, ge=1, le=32767, strict=True)


class SiteDeviceUpdate(Input):
    name: str = Field(min_length=1, max_length=40)
    host: str = Field(min_length=7, max_length=15)
    port: int = Field(default=102, ge=1, le=65535, strict=True)
    rack: int = Field(default=0, ge=0, le=7, strict=True)
    slot: int = Field(default=1, ge=0, le=31, strict=True)
    inbound_dock: int = Field(default=1, ge=1, le=32767, strict=True)
    outbound_dock: int = Field(default=1, ge=1, le=32767, strict=True)
    enabled: bool = Field(strict=True)
    expected_revision: int = Field(ge=1, strict=True)


def create_app(config=None, data_dir=None, reader=None, key=None, commands_path=None, automatic=True, config_path=None, shared_users=None, _managed=False):
    if config is None:
        config_path = Path(config_path or ROOT / 'config/wms.json')
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    data_dir = Path(data_dir or ROOT / 'data/wms')
    data_dir.mkdir(parents=True, exist_ok=True)
    store = WmsStore(data_dir / 'warehouse.sqlite3', config)
    reader = reader or WmsReader(config)
    enabled = config.get('profile') == 'PHYSICAL_CONTROL' and config.get('control_enabled') is True and config.get('device_enabled',True) is True
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
    users = shared_users or Users(data_dir / 'users.sqlite3')
    service.aging = wms_aging.AgingRunner(service, users)

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
        sites = getattr(app.state, 'sites', None)
        if sites and not _managed:
            await sites.start(automatic)
        worker = asyncio.create_task(sample()) if automatic and not sites else None
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
        if sites and not _managed:
            await sites.close()
        else:
            await asyncio.to_thread(close)

    app = FastAPI(title='常规仓储 WMS', version='1.8.0', lifespan=lifespan)
    app.state.store, app.state.service = store, service
    app.state.device = device
    app.state.users = users

    def require(user, permission):
        allowed = user['manage_users'] if permission == 'users' else permission in user['permissions']
        if not allowed:
            raise AuthError('当前用户没有' + ('用户管理' if permission == 'users' else PERMISSIONS[permission]) + '权限', 403)

    def task_permission(user, task, control=False):
        require(user, 'inventory' if task['kind'] in {'MOVE', 'AGING'} else (task.get('document_kind') or task.get('kind', '')).lower())
        if control:
            require(user, 'control')

    def route_path(request):
        path = request.url.path
        root = request.scope.get('root_path','')
        return path[len(root):] if root and path.startswith(root + '/') else path

    async def authorize_write(request, user):
        path = route_path(request)
        if path.startswith(('/api/users','/api/warehouses','/api/devices')) or path == '/api/outbound-scan-policy':
            require(user, 'users')
        elif path == '/api/aging/start':
            require(user, 'inventory')
            require(user, 'control')
        elif path == '/api/aging/stop':
            require(user, 'control')
        elif path in ('/api/auth/logout', '/api/auth/password', '/api/auth/check'):
            return
        elif path in ('/api/tasks', '/api/tasks/batch', '/api/orders', '/api/orders/batch'):
            try:
                body = await request.json()
            except ValueError:
                return  # Pydantic reports malformed JSON without performing a write.
            if not isinstance(body, dict):
                return
            batch = body.get('tasks' if '/tasks' in path else 'orders', []) if path.endswith('/batch') else [body]
            if isinstance(batch, list):
                for row in batch:
                    if isinstance(row, dict) and row.get('kind') in ('INBOUND', 'OUTBOUND'):
                        require(user, row['kind'].lower())
        elif path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) != 5 or parts[4] not in ('cancel', 'dispatch', 'acknowledge', 'resolve'):
                raise AuthError('操作不在允许范围内', 403)
            task = next((t for t in store.read()['tasks'] if t['id'] == parts[3]), None)
            if task:
                task_permission(user, task, parts[4] != 'cancel')
            if parts[4] != 'cancel' and not service.enabled:
                raise AuthError('设备连接与控制尚未启用',409)
        elif path.startswith('/api/orders/') and path.endswith('/return'):
            order = next((o for o in store.read()['orders'] if o['id'] == path.split('/')[3]), None)
            if order:
                task_permission(user, order, True)
            if not service.enabled:
                raise AuthError('设备连接与控制尚未启用',409)
        elif path.startswith('/api/locations') or path == '/api/allocation-policy':
            require(user, 'locations')
        elif path in ('/api/stocktake', '/api/inventory/material', '/api/materials/import/preview', '/api/materials/import', '/api/transfers'):
            require(user, 'inventory')
        elif path == '/api/handover':
            require(user, 'outbound')
        elif path == '/api/device/settings':
            require(user, 'device')
        elif path in ('/api/device/stop', '/api/device/acknowledge'):
            require(user, 'control')
        else:
            raise AuthError('操作不在允许范围内', 403)

    @app.middleware('http')
    async def operator_access(request: Request, call_next):
        path = route_path(request)
        if not _managed and path.startswith('/api/scopes/'):
            # Mounted workspace handles authentication, CSRF and permissions itself.
            return await call_next(request)
        writing = request.method not in ('GET', 'HEAD', 'OPTIONS')
        public = path in ('/api/auth/status', '/api/auth/login', '/api/auth/bootstrap', '/api/health')
        request.state.user = None
        try:
            if writing:
                origin = request.headers.get('origin')
                if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
                    raise AuthError('拒绝跨来源操作', 403)
            if path.startswith('/api/') and not public:
                session = users.session(request.cookies.get(COOKIE, ''))
                request.state.user = session['user']
                request.state.csrf = session['csrf_token']
                if path.startswith('/api/users'):
                    require(session['user'], 'users')
                if writing:
                    supplied = request.headers.get('x-csrf-token', '')
                    if not secrets.compare_digest(supplied.encode(), session['csrf_token'].encode()):
                        raise AuthError('页面验证已失效，请刷新后重试', 403)
                    await authorize_write(request, session['user'])
            elif writing and public:
                if request.headers.get('x-wms-request') != '1':
                    raise AuthError('请从 WMS 登录页面提交', 403)
        except AuthError as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status, headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        if writing and request.state.user and response.status_code < 400 and not path.startswith('/api/auth/') and not path.startswith('/api/users'):
            endpoint = request.scope.get('endpoint')
            action = {
                'create':'登记料箱任务', 'create_batch':'批量登记料箱任务',
                'create_order':'登记物料单据', 'create_order_batch':'批量登记物料单据',
                'create_transfer':'登记调仓任务',
                'start_aging':'启动老化测试', 'stop_aging':'停止老化循环',
                'return_order':'下发返库', 'cancel':'取消任务', 'dispatch':'下发任务',
                'acknowledge':'确认任务回执', 'resolve':'处理任务异常',
                'add_location':'添加库位', 'configure_rack':'配置货架',
                'configure_allocation':'修改分配规则', 'verify_empty':'确认空库位',
                'location_state':'修改库位状态', 'stocktake':'库存盘点调整',
                'handover':'确认提走', 'update_material':'修订物料信息',
                'import_materials':'导入物料信息', 'preview_materials':'预览物料导入',
                'device_settings':'修改设备连接', 'stop':'请求设备停止',
                'configure_scan_policy':'修改出库扫码设置',
                'external_ack':'确认外部设备回执',
            }.get(getattr(endpoint, '__name__', ''), '仓储操作')
            if path.startswith('/api/warehouses'):
                action = '修改仓库' if request.path_params else '添加仓库'
            elif path.startswith('/api/devices'):
                action = '修改设备' if request.path_params else '添加设备'
            scope = getattr(app.state,'scope_id','primary')
            users.record_operation(request.state.user['username'], action,
                                   scope + ' / ' + request.path_params.get('ident', ''))
        if path.startswith('/api/') or path == '/':
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.exception_handler(AuthError)
    async def auth_error(request, error):
        return JSONResponse({'detail': error.detail}, status_code=error.status)

    @app.get('/api/auth/status')
    def auth_status():
        return dict(initialized=users.initialized(), roles=ROLES, permissions=PERMISSIONS, defaults=DEFAULTS)

    def session_response(request, username, password):
        token, session = users.login(username, password, request.client.host if request.client else 'local')
        response = JSONResponse(session)
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            samesite='strict', secure=request.url.scheme == 'https', path='/')
        return response

    @app.post('/api/auth/bootstrap', status_code=201)
    def bootstrap(body: BootstrapInput, request: Request):
        if users.initialized():
            raise AuthError('管理员已初始化，请使用账号登录', 409)
        users.throttle('bootstrap', request.client.host if request.client else 'local')
        if not secrets.compare_digest(body.control_key.encode(), key.encode()):
            raise AuthError('后台控制密钥不正确', 401)
        users.create(body.username, body.display_name, body.password, 'admin', [], '后台主机', bootstrap=True)
        response = session_response(request, body.username, body.password)
        response.status_code = 201
        return response

    @app.post('/api/auth/login')
    def login(body: LoginInput, request: Request):
        return session_response(request, body.username, body.password)

    @app.get('/api/auth/me')
    def me(request: Request):
        return dict(user=request.state.user, csrf_token=request.state.csrf)

    @app.post('/api/auth/logout')
    def logout(request: Request):
        users.logout(request.cookies.get(COOKIE, ''), request.state.user['username'])
        response = JSONResponse(dict(ok=True))
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.post('/api/auth/password')
    def change_password(body: PasswordInput, request: Request):
        users.change_password(request.state.user, body.old_password, body.new_password)
        response = JSONResponse(dict(ok=True))
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.get('/api/users')
    def list_users():
        return dict(users=users.list(), roles=ROLES, permissions=PERMISSIONS, defaults=DEFAULTS)

    @app.post('/api/users', status_code=201)
    def add_user(body: UserInput, request: Request):
        return users.create(body.username, body.display_name, body.password, body.role, body.permissions, request.state.user['username'])

    @app.post('/api/users/{ident}')
    def edit_user(ident: str, body: UserUpdate, request: Request):
        return users.update(ident, body.display_name, body.role, body.permissions, body.enabled,
                            body.expected_revision, body.password, request.state.user)

    @app.get('/api/users/audit')
    def audit_users():
        return dict(events=users.events())

    @app.exception_handler(WmsError)
    async def domain_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.get('/api/state')
    def state(request: Request):
        ledger = store.read()
        stock = list(ledger['stock'].values())
        stock_by_location = {item['location']: item for item in stock if item['status'] == 'IN_STOCK'}
        tasks_by_location = {}
        for task in ledger['tasks']:
            if task['status'] in ACTIVE:
                for ident in task_locations(task):
                    tasks_by_location.setdefault(ident, []).append(task)
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
        return dict(user=request.state.user, scope=app.state.sites.scope(app.state.scope_id), mode='PHYSICAL_WMS', real_plc=True, write_enabled=service.enabled,
                    device_settings=device.snapshot(),
                    outbound_scan_policy=wms_scan_policy.scan_policy(ledger),
                    aging=wms_aging.snapshot(ledger),
                    auto_acknowledge=service.auto_acknowledge, plc=reader.snapshot(),
                    readiness=service.readiness(), locations=locations, rack_layouts=rack_layouts(ledger), allocation_policy=allocation_policy(ledger), stock=stock,
                    tasks=[task for index, task in enumerate(ledger['tasks']) if index < 200 or task['status'] in ACTIVE], events=ledger['events'][:80],
                    orders=[order for index, order in enumerate(ledger.get('orders', [])) if index < 200 or order['status'] not in {'COMPLETED', 'CANCELLED'}],
                    docks=dict(inbound=config['inbound_dock'], outbound=config['outbound_dock']))

    @app.get('/api/health')
    def health():
        return dict(status='ok', version='1.8.0', features=['empty_bins', 'batch_tasks', 'double_depth', 'auto_acknowledge', 'material_return_orders', 'rack_allocation_policy', 'balanced_allocation', 'device_connection_settings', 'user_login', 'user_permissions', 'multiple_warehouses', 'multiple_devices'], mode='PHYSICAL_WMS', real_plc=True, write_enabled=service.enabled,
                    auto_acknowledge=service.auto_acknowledge,
                    plc={'live': reader.snapshot().get('live', False)}, readiness=service.readiness())

    @app.post('/api/device/settings')
    def device_settings(body: DeviceSettingsInput):
        return app.state.sites.change_host(app.state.scope_id,body.host,body.expected_revision)

    @app.post('/api/outbound-scan-policy')
    def configure_scan_policy(body: ScanPolicyInput):
        return wms_scan_policy.configure(store, body.model_dump())

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

    @app.get('/api/materials')
    def materials():
        return dict(materials=sorted(store.read()['materials'].values(), key=lambda item: item['sku']))

    @app.get('/api/materials/template.xlsx')
    def material_template():
        return Response(wms_material_import.template(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': 'attachment; filename="wms-material-template.xlsx"'})

    @app.post('/api/materials/import/preview')
    def preview_materials(body: MaterialFile):
        return wms_material_import.preview(store.read(), wms_material_import.parse_file(body.filename, body.content))

    @app.post('/api/materials/import')
    def import_materials(body: MaterialImport):
        return wms_material_import.import_materials(store, body.model_dump())

    @app.post('/api/auth/check')
    def auth_check():
        return dict(ok=True, write_enabled=service.enabled)

    def register(operation,body):
        with service.lock:
            if not service.enabled:
                raise WmsError('请先在设置中启用设备连接，再登记入出库任务')
            return operation(body)

    @app.post('/api/tasks', status_code=201)
    def create(body: TaskInput):
        return register(store.create_task,body.model_dump())

    @app.post('/api/transfers', status_code=201)
    def create_transfer(body: TransferInput):
        return register(lambda request: wms_transfer.create(store, request), body.model_dump())

    @app.post('/api/aging/start')
    def start_aging(body: AgingStartInput, request: Request):
        return service.aging.start(body.model_dump(), request.state.user)

    @app.post('/api/aging/stop')
    def stop_aging(body: AgingStopInput):
        return service.aging.request_stop(body.model_dump())

    @app.post('/api/tasks/batch', status_code=201)
    def create_batch(body: TaskBatchInput):
        return register(store.create_tasks,body.model_dump())

    @app.get('/api/orders/options')
    def order_options(sku: str = Query(min_length=1, max_length=80)):
        return store.orders.options(sku.strip())

    @app.post('/api/orders', status_code=201)
    def create_order(body: OrderInput):
        return register(store.orders.create,body.model_dump())

    @app.post('/api/orders/batch', status_code=201)
    def create_order_batch(body: OrderBatchInput):
        return register(store.orders.create_batch,body.model_dump())

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
        if not body.site_ready or not service.enabled:
            raise WmsError('请确认外部任务已处理及现场控制权')
        with service.lock, reader.lock:
            if not service.enabled:
                raise WmsError('设备连接与控制尚未启用')
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
        writer.writerow(['料箱条码', '物料编码', '物料名称', '型号', '规格', '数量', '批次', '库位', '状态', '料箱类型', '伸位', '仓库', '设备'])
        def safe(value):
            text = str(value)
            return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
        ledger = store.read()
        scope = app.state.sites.scope(app.state.scope_id)
        for item in ledger['stock'].values():
            from .wms_contents import contents
            loc = ledger['locations'].get(item['location'])
            for line in contents(item) or [dict(sku='', quantity=0, batch='', material_name='', model='', specification='')]:
                values = {**item, **line, 'location': display_location(item['location'])}
                writer.writerow([safe(values[key]) for key in ('barcode', 'sku', 'material_name', 'model', 'specification', 'quantity', 'batch', 'location', 'status')]
                    + ['空箱' if item['container_type'] == 'EMPTY_BIN' else '物料箱', ('双伸' if loc['depth'] == 2 else '单伸') if loc else '',
                       safe(scope['warehouse_name']),safe(scope['device_name'])])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=inventory.csv'})

    @app.get('/')
    def home():
        return HTMLResponse((ROOT / 'web/dist/wms.html').read_text(encoding='utf-8'))

    app.mount('/static', StaticFiles(directory=ROOT / 'web/dist/static'), name='static')
    if not _managed:
        from .wms_sites import Sites
        def factory(child_config, path, folder, commands):
            return create_app(config=child_config,config_path=path,data_dir=folder,commands_path=commands,
                              automatic=False,key=key,shared_users=users,_managed=True)
        sites = Sites(app,data_dir,config,config_path,commands_path or ROOT/'data/physical-commands.sqlite3',factory)
        app.state.sites = sites

        @app.get('/api/sites')
        def site_list():
            return sites.snapshot()

        @app.post('/api/warehouses',status_code=201)
        def add_warehouse(body: WarehouseInput):
            return sites.warehouse(body.name,body.note)

        @app.post('/api/warehouses/{ident}')
        def edit_warehouse(ident: str, body: WarehouseUpdate):
            return sites.warehouse(body.name,body.note,ident,body.expected_revision)

        @app.post('/api/devices',status_code=201)
        def add_device(body: SiteDeviceInput):
            return sites.add_device(body.model_dump())

        @app.post('/api/devices/{ident}')
        def edit_device(ident: str, body: SiteDeviceUpdate):
            return sites.update_device(ident,body.model_dump())
    return app
