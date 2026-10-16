"""Material documents: retrieve a real box, work at the dock, return to its slot."""
import copy
import hashlib
import uuid

from .wms_store import ACTIVE, WmsError, material_details, now
from .wms_allocation import allocation_key, record_allocation
from .wms_scan_policy import scan_policy
from .wms_contents import contents, set_contents, matches_order


class OrderWorkflow:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def order(state, ident):
        item = next((row for row in state.get('orders', []) if row['id'] == ident), None)
        if item is None:
            raise WmsError('出入库单据不存在')
        return item

    def available(self, state, item):
        loc = state['locations'].get(item['location'])
        return bool(item['status'] == 'IN_STOCK' and not item.get('reserved')
            and loc and loc['enabled'] and loc['verified'] and not loc['reserved']
            and not any(t['barcode'] == item['barcode'] and t['status'] in ACTIVE for t in state['tasks'])
            and not self.store.access_error(state, loc))

    def options(self, sku):
        state = self.store.read()
        candidates = [box for box in state['stock'].values() if self.available(state, box)]
        return dict(material=state.get('materials', {}).get(sku),
            boxes=[{**box, **line, 'contents':contents(box)} for box in candidates for line in contents(box) if line['sku'] == sku],
            inbound_boxes=[{**box, 'contents':contents(box)} for box in candidates],
            empty_count=sum(box.get('container_type') == 'EMPTY_BIN' and box['quantity'] == 0 for box in candidates))

    def create(self, request):
        return self.store.once(request['request_id'], {'operation':'material-order', **request},
                               lambda state: self._create(state, request))

    def create_inbound(self, request):
        """One document and one assigned box for all submitted material lines."""
        def apply(state):
            if not 1 <= len(request['items']) <= 50:
                raise WmsError('一张入库单需添加 1 至 50 条物料')
            rows = [dict(**item, kind='INBOUND', box_mode=request['box_mode'],
                         box_barcode=request['box_barcode'], request_id='inbound-line-' +
                         hashlib.sha256(f'{request["request_id"]}:{index}'.encode()).hexdigest())
                    for index, item in enumerate(request['items'])]
            order = self._create(state, rows[0], rows)
            order['document_request_id'] = request['request_id']
            return order
        return self.store.once(request['request_id'], {'operation':'inbound-document', **request}, apply)

    def create_batch(self, request):
        def apply(state):
            if not 1 <= len(request['orders']) <= 50:
                raise WmsError('每次提交 1 至 50 条单据')
            if len({row['request_id'] for row in request['orders']}) != len(request['orders']):
                raise WmsError('单据清单中有重复请求编号')
            groups, by_box = [], {}
            for index, row in enumerate(request['orders'], 1):
                barcode = row.get('box_barcode') if row.get('box_mode') == 'EXISTING' or row['kind'] == 'OUTBOUND' else ''
                if barcode and barcode in by_box:
                    group = by_box[barcode]
                    if group[0][1]['kind'] != row['kind']:
                        raise WmsError('同一箱的清单不能混合入库和出库')
                    group.append((index, row))
                else:
                    group = [(index, row)]
                    groups.append(group)
                    if barcode:
                        by_box[barcode] = group
            result = []
            for group in groups:
                try:
                    result.append(self._create(state, group[0][1], [row for _, row in group]))
                except WmsError as error:
                    raise WmsError(f'第 {group[0][0]} 条：{error}；整批未保存') from error
            return dict(orders=result, count=len(result), line_count=len(request['orders']))
        return self.store.once(request['request_id'], {'operation':'material-order-batch', **request}, apply)

    def _create(self, state, request, rows=None):
        rows = rows or [request]
        existing = next((o for o in state.get('orders', []) if any(
            row['request_id'] == request['request_id'] for row in o.get('request_rows', [o['request']]))), None)
        if existing:
            recorded = existing.get('request_rows', [existing['request']])
            if request not in recorded or (len(rows) > 1 and recorded != rows):
                raise WmsError('同一单据请求编号不能提交不同内容')
            return existing
        if any(any(row['request_id'] == prior['request_id']
                   for prior in order.get('request_rows', [order['request']]))
               for row in rows for order in state.get('orders', [])):
            raise WmsError('清单中包含已经登记的单据行，请勿重复提交')
        kind, mode = request['kind'], request.get('box_mode', 'NEW')
        if kind not in {'INBOUND', 'OUTBOUND'}:
            raise WmsError('请选择出入库类型')
        if kind == 'INBOUND' and mode == 'NEW':
            if request.get('box_barcode'):
                raise WmsError('新箱由系统分配在库空箱，无需填写箱号')
            boxes = [box for box in state['stock'].values() if box.get('container_type') == 'EMPTY_BIN'
                     and not contents(box) and self.available(state, box)]
            boxes.sort(key=lambda box: allocation_key(state, state['locations'][box['location']], inbound=False) + (box['barcode'],))
            if not boxes:
                raise WmsError('没有可用的在库空箱，请先盘点登记或完成空箱入库')
            box = boxes[0]
        elif mode == 'EXISTING' or kind == 'OUTBOUND':
            box = state['stock'].get(request.get('box_barcode'))
            if not box:
                raise WmsError('请选择该物料的在库料箱' if kind == 'OUTBOUND' else '所选料箱不存在，请重新选择')
            mode = 'EXISTING'
        else:
            raise WmsError('请选择系统分配空箱或已有料箱')
        if not self.available(state, box):
            raise WmsError('料箱已预留、库位停用或通道不可用，请重新选择')
        if kind == 'INBOUND':
            for row in rows:
                self.store.register_material(state, row)
        before_contents = contents(box)
        after_contents = copy.deepcopy(before_contents)
        lines = []
        for row in rows:
            sku, amount = row.get('sku', '').strip(), row['quantity']
            if not sku or type(amount) is not int or not 1 <= amount <= 1000000:
                raise WmsError('物料编码不能为空，数量须为大于零的整数')
            current = next((line for line in after_contents if line['sku'] == sku), None)
            before = current['quantity'] if current else 0
            if kind == 'INBOUND':
                details = self.store.register_material(state, row)
                batch = row.get('batch', '')
                if current:
                    if batch and batch != current.get('batch', ''):
                        raise WmsError('已有料箱中该物料的批次不同，请选择另一箱')
                    batch = current.get('batch', '')
                after = before + amount
            else:
                if not current:
                    raise WmsError('请选择该物料的在库料箱')
                details, batch = material_details(current), current.get('batch', '')
                after = before - amount
            if not 0 <= after <= 1000000:
                raise WmsError('出库数量超过箱内该物料库存或入库后数量超过上限')
            if current:
                after_contents.remove(current)
            if after:
                after_contents.append(dict(sku=sku, quantity=after, batch=batch, **details))
            lines.append(dict(sku=sku, quantity=amount, batch=batch, **details,
                              before_quantity=before, after_quantity=after))
        projected = {}
        set_contents(projected, after_contents)
        first = lines[0]
        state['order_sequence'] = state.get('order_sequence', 0) + 1
        order = dict(id=str(uuid.uuid4()), request_id=request['request_id'], request=copy.deepcopy(request),
            request_rows=copy.deepcopy(rows), lines=lines, before_contents=before_contents,
            after_contents=after_contents, box_before_total=box['quantity'], box_after_total=projected['quantity'],
            number=f'{"RK" if kind == "INBOUND" else "CK"}-{state["order_sequence"]:06}',
            kind=kind, sku=first['sku'], **material_details(first), batch=first['batch'],
            quantity=sum(line['quantity'] for line in lines), box_mode=mode,
            barcode=box['barcode'], location=box['location'], before_quantity=first['before_quantity'],
            after_quantity=first['after_quantity'], original_sku=box['sku'], status='QUEUED',
            quantity_posted=False, created_at=now(), updated_at=now())
        fetch = self.store._create_task(state, dict(request_id=request['request_id'], kind='OUTBOUND',
            barcode=box['barcode'], container_type=box.get('container_type', 'MATERIAL')))
        fetch.update(order_id=order['id'], order_leg='FETCH', document_kind=kind)
        order.update(fetch_task_id=fetch['id'], current_task_id=fetch['id'])
        state.setdefault('orders', []).insert(0, order)
        if kind == 'INBOUND' and mode == 'NEW':
            record_allocation(state, state['locations'][box['location']])
        self.store.event(state, f'{order["number"]} 已分配料箱 {box["barcode"]}；{len(lines)} 行物料，待取箱', fetch['id'], created=True)
        return order

    def prepare_return(self, ident, request):
        def apply(state):
            order = self.order(state, ident)
            if order['status'] not in {'WAIT_LOAD', 'WAIT_PICK', 'WAIT_COUNT'}:
                raise WmsError('单据尚未到人工装料或取料阶段，或已提交返库')
            if not request['confirmed']:
                raise WmsError('请确认已经按单据数量装料或取料，并已核对现场')
            if order['kind'] == 'OUTBOUND':
                policy = scan_policy(state)
                if request.get('expected_scan_revision') is not None and request['expected_scan_revision'] != policy['revision']:
                    raise WmsError('出库扫码设置已变化，请重新核对后提交')
                if policy['require_barcode'] and request.get('scanned_barcode', '').strip() != order['barcode']:
                    raise WmsError('扫描箱号与本次出库料箱不一致')
                required_skus = {line['sku'] for line in order.get('lines', [order])}
                scanned = {value.strip() for value in request.get('scanned_skus', [])}
                scanned.add(request.get('scanned_sku', '').strip())
                if policy['require_sku'] and not required_skus.issubset(scanned):
                    raise WmsError('扫描物料编码与出库单据不一致，请核对全部物料')
            box = state['stock'][order['barcode']]
            fetch = self.store.task(state, order['fetch_task_id'])
            location = state['locations'][order['location']]
            if box['status'] != 'AT_STATION' or box.get('reserved') != fetch['id'] or fetch['status'] != 'AWAIT_RETURN':
                raise WmsError('交接点料箱或任务状态已变化，请重新核对')
            if not location['enabled'] or location['reserved'] != fetch['id']:
                raise WmsError('原库位预留已变化，不能返库')
            if not matches_order(box, order):
                raise WmsError('箱内账面物料或数量已变化，不能按原单据返库')
            reason = self.store.access_error(state, location, inbound=True, task_id=fetch['id'])
            if reason:
                raise WmsError(reason)
            if order['kind'] == 'COUNT':
                from .wms_count import confirm
                confirm(self.store, state, order, request)
            state['sequence'] += 1
            task = dict(id=str(uuid.uuid4()), number=f'WMS-{state["sequence"]:06}', request=copy.deepcopy(request),
                kind='INBOUND', order_id=ident, order_leg='RETURN', document_kind=order['kind'],
                barcode=order['barcode'], sku=order['sku'], quantity=order.get('box_after_total', order['after_quantity']), batch=order['batch'],
                container_type='MATERIAL' if order.get('box_after_total', order['after_quantity']) else 'EMPTY_BIN', **material_details(order),
                location=order['location'], dock=self.store.config['inbound_dock'], status='QUEUED', plc_id=None,
                detail='已确认人工操作，等待返库下发', created_at=now(), updated_at=now(), posted=False, ack_pending=False)
            if 'after_contents' in order:
                set_contents(task, order['after_contents'])
            fetch.update(status='COMPLETED', updated_at=now(), detail='交接点人工操作已核对，转入返库任务')
            state['tasks'].insert(0, task)
            location['reserved'] = box['reserved'] = task['id']
            order.update(status='RETURN_QUEUED', current_task_id=task['id'], return_task_id=task['id'],
                         station_confirmation=copy.deepcopy(request), updated_at=now())
            if order['kind'] == 'OUTBOUND':
                order['station_confirmation']['scan_policy'] = policy
            self.store.event(state, f'{order["number"]} 人工操作已确认；按单据返库后全箱数量为 {order.get("box_after_total", order["after_quantity"])}', task['id'], created=True)
            return task
        return self.store.once(request['request_id'], {'operation':'order-return', 'order_id':ident, **request}, apply)

    def posted(self, state, task):
        order = self.order(state, task['order_id'])
        if order['current_task_id'] != task['id']:
            raise WmsError('回执对应的搬运任务不是单据当前步骤')
        box = state['stock'][order['barcode']]
        if task['order_leg'] == 'FETCH':
            box.update(status='AT_STATION', location='STATION-1', home_location=order['location'],
                       reserved=task['id'], updated_at=now())
            order['status'] = 'FETCH_ACK'
        else:
            if not order['quantity_posted']:
                if not matches_order(box, order):
                    raise WmsError('返库数量过账前库存发生变化，请核对')
                if 'after_contents' in order:
                    rows = [{**line, **material_details(state['materials'].get(line['sku'], line))}
                            for line in order['after_contents']]
                    set_contents(box, rows)
                elif order['after_quantity']:
                    catalog = state['materials'].get(order['sku'], order)
                    box.update(container_type='MATERIAL', sku=order['sku'], quantity=order['after_quantity'],
                               batch=order['batch'], **material_details(catalog))
                else:
                    box.update(container_type='EMPTY_BIN', sku='', quantity=0, batch='', **material_details({}))
                for index, line in enumerate(order.get('lines', [order])):
                    state.setdefault('quantity_movements', []).insert(0, dict(order_id=order['id'], line=index,
                        number=order['number'], barcode=order['barcode'], sku=line['sku'], kind=order['kind'],
                        quantity=line['quantity'], delta=line['after_quantity']-line['before_quantity'],
                        before=line['before_quantity'], after=line['after_quantity'], time=now()))
                order['quantity_posted'] = True
            box.update(status='IN_STOCK', location=order['location'], reserved=task['id'], updated_at=now())
            order['status'] = 'RETURN_ACK'
        order['updated_at'] = now()
        task.update(posted=True, status='PLC_DONE', ack_pending=True, updated_at=now(),
                    detail='搬运完成回执已匹配，等待自动确认')
        self.store.event(state, f'{order["number"]} {"取箱到交接点" if task["order_leg"] == "FETCH" else "返库及数量过账"}完成', task['id'])

    def acknowledged(self, state, task):
        order = self.order(state, task['order_id'])
        if task['order_leg'] == 'FETCH':
            task.update(status='AWAIT_RETURN', detail='请核对箱内实际物料及数量，确认盘点后返库' if order['kind'] == 'COUNT' else '请按单据装料后返库' if order['kind'] == 'INBOUND' else '请扫码核对箱号及物料，按单据取料后返库')
            order['status'] = {'COUNT':'WAIT_COUNT','INBOUND':'WAIT_LOAD','OUTBOUND':'WAIT_PICK'}[order['kind']]
        else:
            self.store.release(state, task)
            task.update(status='COMPLETED', detail='返库完成，已按单据更新库存数量')
            order.update(status='COMPLETED', completed_at=now())
        order['updated_at'] = now()

    def cancelled(self, state, task):
        order = self.order(state, task['order_id'])
        if task['order_leg'] == 'FETCH':
            self.store.release(state, task)
            order['status'] = 'CANCELLED'
        else:
            fetch = self.store.task(state, order['fetch_task_id'])
            fetch.update(status='AWAIT_RETURN', updated_at=now(), detail='返库搬运已取消，人工操作记录保留；请勿重复装料或取料，核对后重新返库')
            state['locations'][order['location']]['reserved'] = state['stock'][order['barcode']]['reserved'] = fetch['id']
            order.update(status={'COUNT':'WAIT_COUNT','INBOUND':'WAIT_LOAD','OUTBOUND':'WAIT_PICK'}[order['kind']], current_task_id=fetch['id'])
        order['updated_at'] = now()
        task.update(status='CANCELLED', updated_at=now(), detail='本次搬运已取消')
