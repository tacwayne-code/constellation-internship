"""Physical WMS ledger. Every reservation and inventory posting is atomic."""
import copy
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .wms_allocation import allocation_policy, allocation_key, validate_policy, record_allocation, DEFAULT_POLICY


def now():
    return datetime.now(timezone.utc).isoformat()


class WmsError(Exception):
    pass


def location_id(values):
    return f'{"L" if values["side"] == 1 else "R"}-{values["column"]:02}-{values["level"]:02}-{values["depth"]}'


def display_location(ident):
    """Format side-level-column without changing persisted IDs or PLC coordinates."""
    import re
    return re.sub(r'^([LR])-(\d{2})-(\d{2})-[12]$', r'\1-\3-\2', ident)


ACTIVE = {'QUEUED', 'DISPATCHING', 'SENT', 'RUNNING', 'REVIEW', 'PLC_DONE', 'AWAIT_PICKUP', 'AWAIT_RETURN'}
IN_FLIGHT = {'DISPATCHING', 'SENT', 'RUNNING', 'REVIEW', 'PLC_DONE'}
MATERIAL_FIELDS = ('material_name', 'model', 'specification')
CLOSED_STOCK = {'SHIPPED', 'REMOVED'}


def material_details(record):
    return {key: record.get(key, '') for key in MATERIAL_FIELDS}


def location_token(location, stock, tasks):
    if stock:
        stock = {**stock, **material_details(stock), 'container_type': stock.get('container_type', 'MATERIAL')}
    value = dict(location=location, stock=stock,
        tasks=sorted((dict(id=task['id'], status=task['status'], updated_at=task.get('updated_at')) for task in tasks), key=lambda item: item['id']))
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rack_layouts(state):
    layouts = []
    for side in (1, 2):
        active = [loc for loc in state['locations'].values() if loc['side'] == side and loc['enabled']]
        saved = state.get('rack_layouts', {}).get(str(side), {})
        layouts.append(dict(side=side,
            levels=max(saved.get('levels', 0), max((loc['level'] for loc in active), default=0)),
            columns=max(saved.get('columns', 0), max((loc['column'] for loc in active), default=0)),
            depths=max(saved.get('depths', 1), max((loc['depth'] for loc in active), default=1)),
            revision=saved.get('revision', 0)))
    return layouts


class WmsStore:
    @property
    def orders(self):
        from .wms_orders import OrderWorkflow
        return OrderWorkflow(self)

    def __init__(self, path, config):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config = config
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM ledger').fetchone():
                locations = {}
                for values in config.get('initial_locations', []):
                    ident = location_id(values)
                    locations[ident] = dict(id=ident, **values, verified=False, enabled=True, reserved=None)
                state = dict(schema=1, sequence=0, next_plc_id=1000, locations=locations,
                             stock={}, tasks=[], events=[], receipts={}, materials={}, material_catalog_version=1)
                self.event(state, '仓储账本已建立；无演示库存，请核对库位或登记现有库存。')
                db.execute('INSERT INTO ledger VALUES (1,?)', (json.dumps(state, ensure_ascii=False),))
            state = json.loads(db.execute('SELECT payload FROM ledger WHERE id=1').fetchone()[0])
            if not state.get('material_catalog_version'):
                # Backfill once, including shipped stock and completed task snapshots.
                # Task completion time is not the time its material snapshot was captured.
                records = [(task.get('created_at', ''), task) for task in state['tasks'] if task.get('posted')]
                records += [(item.get('updated_at', item.get('received_at', '')), item) for item in state['stock'].values()]
                materials = state.setdefault('materials', {})
                for stamp, item in sorted(records, key=lambda row: row[0]):
                    sku = item.get('sku', '').strip()
                    if not sku:
                        continue
                    record = materials.setdefault(sku, dict(sku=sku, **material_details({}), created_at=stamp))
                    record.update({key: value for key, value in material_details(item).items() if value})
                    record['updated_at'] = stamp
                state['material_catalog_version'] = 1
                self.event(state, f'已从历史记录建立 {len(materials)} 个物料编码档案；库存清零后保留')
                db.execute('UPDATE ledger SET payload=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def read(self):
        with self.lock, self.db() as db:
            state = json.loads(db.execute('SELECT payload FROM ledger WHERE id=1').fetchone()[0])
            # Legacy stock/tasks stay valid without rewriting historical records.
            for item in [*state['stock'].values(), *state['tasks']]:
                item.update(material_details(item))
                item.setdefault('container_type', 'MATERIAL')
            return state

    def mutate(self, fn):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            before = db.execute('SELECT payload FROM ledger WHERE id=1').fetchone()[0]
            state = json.loads(before)
            result = fn(state)
            self.validate(state)
            after = json.dumps(state, ensure_ascii=False)
            if after != before:
                db.execute('UPDATE ledger SET payload=? WHERE id=1', (after,))
            return copy.deepcopy(result)

    @staticmethod
    def event(state, message, task=None):
        state['events'].insert(0, dict(id=str(uuid.uuid4()), time=now(), message=message, task=task))
        state['events'] = state['events'][:2000]

    @staticmethod
    def task(state, ident):
        task = next((item for item in state['tasks'] if item['id'] == ident), None)
        if task is None:
            raise WmsError('任务不存在')
        return task

    @staticmethod
    def validate(state):
        occupied = [item['location'] for item in state['stock'].values() if item['status'] == 'IN_STOCK']
        if len(set(occupied)) != len(occupied):
            raise WmsError('同一库位不能登记两个料箱')
        if len([item for item in state['stock'].values() if item['status'] in {'AT_EXIT', 'AT_STATION'}]) > 1:
            raise WmsError('出口尚有料箱，请扫码确认取走')
        for location in state['locations'].values():
            if location['reserved']:
                task = WmsStore.task(state, location['reserved'])
                if task['status'] not in ACTIVE:
                    raise WmsError('库位预留与任务状态不一致')
        for order in state.get('orders', []):
            if order['status'] not in {'COMPLETED', 'CANCELLED'}:
                task = WmsStore.task(state, order['current_task_id'])
                if task['status'] not in ACTIVE or state['locations'][order['location']]['reserved'] != task['id']:
                    raise WmsError('物料单据的原库位预留与当前任务不一致')

    def once(self, key, payload, apply):
        def comparable(value):
            # Empty optional fields must not invalidate retries from older clients.
            defaults = dict.fromkeys(MATERIAL_FIELDS, '') | dict(container_type='MATERIAL', depth=0, depths=1,
                                                                 allocation=None, expected_allocation_revision=None)
            return {name: item for name, item in value.items() if name not in defaults or item != defaults[name]}
        def update(state):
            previous = state['receipts'].get(key)
            if previous:
                if comparable(previous['payload']) != comparable(payload):
                    raise WmsError('同一请求编号不能提交不同内容')
                return previous['result']
            result = apply(state)
            state['receipts'][key] = dict(payload=payload, result=copy.deepcopy(result))
            return result
        return self.mutate(update)

    def recover(self):
        def update(state):
            for task in state['tasks']:
                if task['status'] in {'DISPATCHING', 'SENT', 'RUNNING'}:
                    task.update(status='REVIEW', detail='后台重启，保留预留并核对 PLC；不自动重发', updated_at=now())
                    if task.get('order_id'):
                        self.orders.order(state, task['order_id']).update(status='REVIEW', updated_at=now())
                    self.event(state, task['detail'], task['id'])
        self.mutate(update)

    def add_location(self, request):
        def apply(state):
            values = {key: request[key] for key in ('side', 'level', 'column')}
            depth = request.get('depth')
            if depth is not None and depth not in (1, 2):
                raise WmsError('请选择单伸或双伸库位')
            depths = [depth] if depth is not None else range(1, rack_layouts(state)[values['side']-1]['depths']+1)
            created = []
            for selected_depth in depths:
                ident = location_id({**values, 'depth': selected_depth})
                if ident in state['locations']:
                    if depth is not None:
                        raise WmsError('库位已存在')
                    continue
                item = dict(id=ident, **values, depth=selected_depth, verified=request['verified_empty'], enabled=True, reserved=None)
                state['locations'][ident] = item
                created.append(item)
                self.event(state, f'新增库位 {ident}；' + ('已核对为空' if item['verified'] else '待现场核对'))
            if not created:
                raise WmsError('该层列位置的库位已存在')
            layout = rack_layouts(state)[values['side']-1]
            layout['revision'] += 1
            state.setdefault('rack_layouts', {})[str(values['side'])] = layout
            return created[0] if depth is not None else dict(locations=created, count=len(created))
        return self.once(request['request_id'], {'operation': 'location', **request}, apply)

    def configure_rack(self, request):
        def apply(state):
            side, levels, columns = request['side'], request['levels'], request['columns']
            current = rack_layouts(state)[side-1]
            depths = request.get('depths', current['depths'])
            if current['revision'] != request['expected_revision']:
                raise WmsError('库位设置已改变，请关闭设置窗口后重新打开')
            policy = request.get('allocation')
            if policy is not None:
                try:
                    policy = validate_policy(policy)
                except ValueError as error:
                    raise WmsError(str(error)) from error
                current_policy = allocation_policy(state)
                if request.get('expected_allocation_revision') != current_policy['revision']:
                    raise WmsError('全仓分配规则已被修改，请关闭设置窗口后重新打开')
            outside = [loc for loc in state['locations'].values()
                       if loc['side'] == side and (loc['level'] > levels or loc['column'] > columns or loc['depth'] > depths)]
            occupied = {item['location'] for item in state['stock'].values() if item['status'] == 'IN_STOCK'}
            task_locations = {task['location'] for task in state['tasks'] if task['status'] in ACTIVE}
            blocked = [loc['id'] for loc in outside if loc['reserved'] or loc['id'] in occupied or loc['id'] in task_locations]
            if blocked:
                raise WmsError('以下库位有库存或未结束任务，不能缩出范围：' + '、'.join(blocked[:10]))
            disabled = 0
            for loc in outside:
                if loc['enabled']:
                    disabled += 1
                    loc['enabled'] = False
            added, restored = 0, 0
            for level in range(1, levels+1):
                for column in range(1, columns+1):
                    for depth in range(1, depths+1):
                        values = dict(side=side, level=level, column=column, depth=depth)
                        ident = location_id(values)
                        item = state['locations'].get(ident)
                        if item is None:
                            state['locations'][ident] = dict(id=ident, **values, enabled=True,
                                verified=request['verified_empty'], reserved=None)
                            added += 1
                        elif not item['enabled'] and not item.get('manual_disabled'):
                            item.update(enabled=True, verified=request['verified_empty'])
                            restored += 1
            layout = dict(side=side, levels=levels, columns=columns, depths=depths, revision=current['revision']+1)
            state.setdefault('rack_layouts', {})[str(side)] = layout
            if policy is not None:
                self.save_allocation(state, policy, current_policy)
            self.event(state, f'{"左" if side == 1 else "右"}仓设为 {levels} 层 {columns} 列{"单伸" if depths == 1 else "双伸（前后排）"}；新增 {added}、重新启用 {restored}、停用 {disabled} 个库位')
            return dict(**layout, added=added, restored=restored, disabled=disabled)
        return self.once(request['request_id'], {'operation': 'rack-layout', **request}, apply)

    def save_allocation(self, state, policy, current):
        if any(policy[key] != current[key] for key in DEFAULT_POLICY):
            if policy['side_order'] == 'BALANCED' and current['side_order'] != 'BALANCED':
                state['allocation_next_side'] = 1
            state['allocation_policy'] = dict(**policy, revision=current['revision']+1)
            self.event(state, '全仓库位分配规则已更新：' + json.dumps(policy, ensure_ascii=False))
        return allocation_policy(state)

    def configure_allocation(self, request):
        def apply(state):
            try:
                policy = validate_policy(request['allocation'])
            except ValueError as error:
                raise WmsError(str(error)) from error
            current = allocation_policy(state)
            if request['expected_revision'] != current['revision']:
                raise WmsError('全仓分配规则已被修改，请关闭设置窗口后重新打开')
            return self.save_allocation(state, policy, current)
        return self.once(request['request_id'], {'operation': 'allocation-policy', **request}, apply)

    def manage_location(self, ident, request):
        def apply(state):
            location = state['locations'].get(ident)
            if location is None:
                raise WmsError('库位不存在')
            item = next((item for item in state['stock'].values() if item['location'] == ident and item['status'] == 'IN_STOCK'), None)
            tasks = [task for task in state['tasks'] if task['location'] == ident and task['status'] in ACTIVE]
            if not request['confirmed']:
                raise WmsError('请核对现场情况并确认本次库位操作')
            if request['expected_token'] != location_token(location, item, tasks):
                raise WmsError('库位或库存已变化，请取消本次操作后重新选择')
            if location['reserved'] or tasks or (item and item.get('reserved')):
                raise WmsError('库位有未结束任务或预留，请先在任务页处理；不能直接解除占用或停启用')
            action = request['action']
            before = dict(location=copy.deepcopy(location), stock=copy.deepcopy(item))
            # Freeze inferred legacy bounds before disabling an edge location.
            layout = rack_layouts(state)[location['side']-1]
            if action == 'DISABLE':
                if not location['enabled']:
                    raise WmsError('库位已经停用')
                location.update(enabled=False, manual_disabled=True)
                detail = '停用库位，保留库存并禁止出入库分配'
            elif action == 'ENABLE':
                if location['enabled']:
                    raise WmsError('库位已经启用')
                if location['level'] > layout['levels'] or location['column'] > layout['columns'] or location['depth'] > layout['depths']:
                    raise WmsError('库位超出当前总层列范围，请先调整总层列数')
                location.update(enabled=True, manual_disabled=False, verified=bool(item))
                detail = '启用库位；' + ('保留当前库存' if item else '空位须重新现场核对')
            elif action == 'RELEASE':
                if item is None:
                    raise WmsError('库位没有在库占用，无需解除')
                item.update(status='REMOVED', location='REMOVED', previous_location=ident, reserved=None,
                    updated_at=now(), removed_at=now(), removal_note=request['note'])
                location['verified'] = True
                detail = f'人工解除占用：料箱 {item["barcode"]}，物料 {item["sku"]}，数量 {item["quantity"]}；已确认现场为空'
            else:
                raise WmsError('不支持的库位操作')
            location['management_revision'] = location.get('management_revision', 0) + 1
            layout['revision'] += 1
            state.setdefault('rack_layouts', {})[str(location['side'])] = layout
            # Keep a full immutable adjustment even if the box barcode is reused later.
            state.setdefault('location_adjustments', []).insert(0, dict(id=request['request_id'], time=now(),
                location=ident, action=action, note=request['note'], before=before,
                after=dict(location=copy.deepcopy(location), stock=copy.deepcopy(item))))
            self.event(state, f'{ident} {detail}；原因：{request["note"]}')
            return dict(location=location, action=action)
        return self.once(request['request_id'], {'operation':'location-state', 'location':ident, **request}, apply)

    def verify_empty(self, ident):
        def update(state):
            location = state['locations'].get(ident)
            if location is None:
                raise WmsError('库位不存在')
            if not location['enabled']:
                raise WmsError('库位已停用，请先在库位详情中重新启用')
            if location['reserved'] or any(task['location'] == ident and task['status'] in ACTIVE for task in state['tasks']) or any(item['location'] == ident and item['status'] not in CLOSED_STOCK for item in state['stock'].values()):
                raise WmsError('库位存在库存或预留，不能直接改为空位')
            location['verified'] = True
            self.event(state, f'现场核对库位为空：{ident}')
            return location
        return self.mutate(update)

    def stocktake(self, request):
        def apply(state):
            ident, barcode = request['location'], request['barcode']
            location = state['locations'].get(ident)
            if not location or not location['enabled'] or location['reserved']:
                raise WmsError('请选择已建档、启用且未被预留的库位')
            if any(item['location'] == ident and item['status'] == 'IN_STOCK' for item in state['stock'].values()):
                raise WmsError('该库位已经有库存')
            if barcode in state['stock'] and state['stock'][barcode]['status'] not in CLOSED_STOCK:
                raise WmsError('该料箱已有库存记录')
            if any(t['barcode'] == barcode and t['status'] in ACTIVE for t in state['tasks']):
                raise WmsError('该料箱存在未结束任务')
            item = dict(barcode=barcode, **self.goods_details(state, request),
                        location=ident, status='IN_STOCK', reserved=None, received_at=now(), updated_at=now())
            state['stock'][barcode] = item
            location['verified'] = True
            self.event(state, f'盘点登记现有料箱 {barcode} 到 {ident}；{request["note"]}')
            return item
        return self.once(request['request_id'], {'operation': 'stocktake', **request}, apply)

    @staticmethod
    def goods_details(state, request):
        container_type = request.get('container_type', 'MATERIAL')
        if container_type == 'EMPTY_BIN':
            if request.get('sku') or any(material_details(request).values()) or request.get('batch'):
                raise WmsError('空箱不填写物料、型号、规格或批次，请清空后提交')
            return dict(container_type=container_type, sku='', quantity=0, batch='', **material_details({}))
        if container_type != 'MATERIAL' or request.get('quantity', 0) < 1:
            raise WmsError('物料箱数量必须大于零')
        return dict(container_type=container_type, sku=request['sku'].strip(), quantity=request['quantity'],
                    batch=request['batch'], **WmsStore.register_material(state, request))

    @staticmethod
    def access_error(state, location, inbound=False, task_id=None):
        """Double-deep access requires an empty front slot; no automatic reshuffle."""
        if location['depth'] == 2:
            front = state['locations'].get(location_id({**location, 'depth': 1}))
            if not front or not front['enabled'] or not front['verified']:
                return '双伸前排单伸库位需已建档、启用并核对'
            if front['reserved'] or any(item['location'] == front['id'] and item['status'] == 'IN_STOCK' for item in state['stock'].values()):
                return '双伸前排单伸库位有占用或预留，请先清空前排'
            if any(task['location'] == front['id'] and task['status'] in ACTIVE for task in state['tasks']):
                return '双伸前排存在未结束任务，请先完成前排任务'
        elif inbound:
            rear_id = location_id({**location, 'depth': 2})
            if any(task['id'] != task_id and task['location'] == rear_id and task['status'] in ACTIVE for task in state['tasks']):
                return '同位置双伸后排有待办任务，请先处理后排再向单伸前排入库'
        return ''

    @staticmethod
    def register_material(state, request):
        sku = request['sku'].strip()
        if not sku:
            raise WmsError('请填写物料编码')
        materials = state.setdefault('materials', {})
        existing = materials.get(sku)
        supplied = material_details(request)
        if existing:
            if any(existing.get(key) and value and existing[key] != value for key, value in supplied.items()):
                raise WmsError('物料编码已有不同的名称、型号或规格，请重新扫码使用档案；如需修订，请在库存页编辑物料信息')
            details = {key: existing.get(key) or supplied[key] for key in MATERIAL_FIELDS}
            if details != material_details(existing):
                existing.update(details, updated_at=now())
                WmsStore.sync_material_stock(state, sku, details)
        else:
            details = supplied
            materials[sku] = dict(sku=sku, **details, created_at=now(), updated_at=now())
            WmsStore.event(state, f'建立物料档案：{sku}')
        return details

    @staticmethod
    def sync_material_stock(state, sku, details):
        for item in state['stock'].values():
            if item['sku'].strip() == sku:
                item.update(details, updated_at=now())

    def update_material(self, request):
        def apply(state):
            item = state['stock'].get(request['barcode'])
            if item is None:
                raise WmsError('料箱库存记录不存在')
            if item.get('container_type') == 'EMPTY_BIN':
                raise WmsError('空箱没有物料档案')
            if item['updated_at'] != request['expected_updated_at']:
                raise WmsError('库存记录已更新，请关闭后重新打开物料信息')
            before = material_details(item)
            after = material_details(request)
            sku = item['sku'].strip()
            if any(o['sku'] == sku and o['status'] not in {'COMPLETED', 'CANCELLED'} for o in state.get('orders', [])):
                raise WmsError('该物料有未结束的装料或取料单据，完成后再修改档案')
            catalog = state.setdefault('materials', {}).setdefault(sku, dict(sku=sku, created_at=now()))
            catalog.update(after, updated_at=now())
            self.sync_material_stock(state, sku, after)
            self.event(state, f'更新物料档案 {sku} 及同编码料箱：{json.dumps(before, ensure_ascii=False)} → {json.dumps(after, ensure_ascii=False)}')
            return item
        return self.once(request['request_id'], {'operation': 'material', **request}, apply)

    def create_task(self, request):
        return self.once(request['request_id'], {'operation': 'task', **request},
                         lambda state: self._create_task(state, request))

    def create_tasks(self, request):
        def apply(state):
            rows = request['tasks']
            if not 1 <= len(rows) <= 50:
                raise WmsError('每次清单提交 1 至 50 条任务')
            if len({row['barcode'] for row in rows}) != len(rows):
                raise WmsError('清单中有重复料箱条码')
            results = []
            for index, row in enumerate(rows, 1):
                try:
                    results.append(self._create_task(state, row))
                except WmsError as error:
                    raise WmsError(f'第 {index} 条：{error}；整批未保存') from error
            return dict(tasks=results, count=len(results))
        return self.once(request['request_id'], {'operation': 'task-batch', **request}, apply)

    def _create_task(self, state, request):
        barcode, kind = request['barcode'], request['kind']
        if any(task['barcode'] == barcode and task['status'] in ACTIVE for task in state['tasks']):
            raise WmsError('该料箱已有未结束任务；请勿重复扫码建单')
        item = state['stock'].get(barcode)
        if kind == 'INBOUND':
            if item and item['status'] not in CLOSED_STOCK:
                raise WmsError('该料箱已有库存，请核对条码')
            occupied = {item['location'] for item in state['stock'].values() if item['status'] == 'IN_STOCK'}
            available = [loc for loc in state['locations'].values()
                         if loc['enabled'] and loc['verified'] and not loc['reserved'] and loc['id'] not in occupied
                         and (not request.get('depth') or loc['depth'] == request['depth'])
                         and not self.access_error(state, loc, inbound=True)]
            # Fill the rear first when both depths are requested and physically accessible.
            available.sort(key=lambda loc: allocation_key(state, loc))
            target = request['location']
            if target == 'AUTO':
                target = available[0]['id'] if available else None
            if target not in {loc['id'] for loc in available}:
                selected = state['locations'].get(target)
                if selected:
                    reason = self.access_error(state, selected, inbound=True)
                    if reason:
                        raise WmsError(reason)
                raise WmsError('没有已核对的可用空位，请核对库位和单双伸选择')
            goods = self.goods_details(state, request)
            location, dock = target, self.config['inbound_dock']
        elif kind == 'OUTBOUND':
            if not item or item['status'] != 'IN_STOCK' or item.get('reserved'):
                raise WmsError('料箱不在可出库库存中')
            location, dock = item['location'], self.config['outbound_dock']
            if not state['locations'][location]['enabled']:
                raise WmsError('源库位已停用，不能创建出库任务；请先在库位页重新启用')
            if state['locations'][location]['reserved']:
                raise WmsError('源库位已经预留')
            if request.get('container_type', 'MATERIAL') != item.get('container_type', 'MATERIAL'):
                raise WmsError('料箱类型与库存不一致，请选择物料箱或空箱后重新读取')
            goods = dict(container_type=item.get('container_type', 'MATERIAL'), sku=item['sku'],
                         quantity=item['quantity'], batch=item['batch'], **material_details(item))
        else:
            raise WmsError('不支持的任务类型')
        state['sequence'] += 1
        task = dict(id=str(uuid.uuid4()), number=f'WMS-{state["sequence"]:06}', request=request,
                    kind=kind, barcode=barcode, **goods,
                    location=location, dock=dock, status='QUEUED', plc_id=None, detail='等待核对并下发',
                    created_at=now(), updated_at=now(), posted=False, ack_pending=False)
        state['tasks'].insert(0, task)
        state['locations'][location]['reserved'] = task['id']
        if kind == 'OUTBOUND':
            item['reserved'] = task['id']
        elif request['location'] == 'AUTO':
            record_allocation(state, state['locations'][location])
        label = ('空箱' if goods['container_type'] == 'EMPTY_BIN' else '') + ('入库' if kind == 'INBOUND' else '出库')
        self.event(state, f'{task["number"]} {label}已登记：{barcode} / {location}', task['id'])
        return task

    def cancel(self, ident):
        def update(state):
            task = self.task(state, ident)
            if task['status'] == 'CANCELLED':
                return task
            if task['status'] != 'QUEUED':
                raise WmsError('仅未下发的任务可以取消；已下发任务需现场核对')
            if task.get('order_id'):
                self.orders.cancelled(state, task)
            else:
                self.release(state, task)
                task.update(status='CANCELLED', detail='下发前取消', updated_at=now())
            self.event(state, f'{task["number"]} 已取消', ident)
            return task
        return self.mutate(update)

    @staticmethod
    def release(state, task):
        state['locations'][task['location']]['reserved'] = None
        item = state['stock'].get(task['barcode'])
        if item and item.get('reserved') == task['id']:
            item['reserved'] = None

    def post_completion(self, state, task, manual=False):
        if task['posted']:
            return
        if task.get('order_id'):
            return self.orders.posted(state, task)
        if task['kind'] == 'INBOUND':
            # Keep task snapshots immutable, but posted stock uses the current catalog.
            material = state.get('materials', {}).get(task['sku'], task)
            state['stock'][task['barcode']] = dict(barcode=task['barcode'], sku=task['sku'], quantity=task['quantity'],
                batch=task['batch'], container_type=task.get('container_type', 'MATERIAL'), **material_details(material), location=task['location'], status='IN_STOCK', reserved=None,
                received_at=now(), updated_at=now())
        else:
            item = state['stock'][task['barcode']]
            item.update(location='OUT-1', status='AT_EXIT', reserved=task['id'], updated_at=now())
        state['locations'][task['location']]['reserved'] = None
        task.update(posted=True, status='PLC_DONE', ack_pending=True, updated_at=now(),
                    detail='完成回执已匹配，库存已更新；待确认 PLC 回执')
        self.event(state, f'{task["number"]} {"人工核对到位" if manual else "PLC 完成回执匹配"}，库存已更新', task['id'])

    def handover(self, request):
        def apply(state):
            item = state['stock'].get(request['barcode'])
            if not item or item['status'] != 'AT_EXIT':
                raise WmsError('扫码料箱不在出口待取区域')
            task = self.task(state, item['reserved'])
            if task['status'] != 'AWAIT_PICKUP':
                raise WmsError('请等待后台确认 PLC 完成回执；异常时在任务页核对处理')
            item.update(status='SHIPPED', location='SHIPPED', reserved=None, updated_at=now())
            task.update(status='COMPLETED', detail='出口扫码交接完成', updated_at=now())
            self.event(state, f'{task["number"]} 出口扫码确认取走：{item["barcode"]}', task['id'])
            return task
        return self.once(request['request_id'], {'operation': 'handover', **request}, apply)
