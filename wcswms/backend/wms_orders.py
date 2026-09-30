"""Material documents: retrieve a real box, work at the dock, return to its slot."""
import copy
import uuid

from .wms_store import ACTIVE, WmsError, material_details, now
from .wms_allocation import allocation_key, record_allocation


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
            boxes=[box for box in candidates if box.get('container_type', 'MATERIAL') == 'MATERIAL' and box['sku'] == sku],
            empty_count=sum(box.get('container_type') == 'EMPTY_BIN' and box['quantity'] == 0 for box in candidates))

    def create(self, request):
        return self.store.once(request['request_id'], {'operation':'material-order', **request},
                               lambda state: self._create(state, request))

    def create_batch(self, request):
        def apply(state):
            if not 1 <= len(request['orders']) <= 50:
                raise WmsError('每次提交 1 至 50 条单据')
            if len({row['request_id'] for row in request['orders']}) != len(request['orders']):
                raise WmsError('单据清单中有重复请求编号')
            result = []
            for index, row in enumerate(request['orders'], 1):
                try:
                    result.append(self._create(state, row))
                except WmsError as error:
                    raise WmsError(f'第 {index} 条：{error}；整批未保存') from error
            return dict(orders=result, count=len(result))
        return self.store.once(request['request_id'], {'operation':'material-order-batch', **request}, apply)

    def _create(self, state, request):
        existing = next((o for o in state.get('orders', []) if o['request_id'] == request['request_id']), None)
        if existing:
            if existing['request'] != request:
                raise WmsError('同一单据请求编号不能提交不同内容')
            return existing
        kind, quantity = request['kind'], request['quantity']
        if kind not in {'INBOUND', 'OUTBOUND'} or type(quantity) is not int or not 1 <= quantity <= 1000000:
            raise WmsError('请选择出入库类型并填写大于零的整数数量')
        sku = request.get('sku', '').strip()
        if not sku:
            raise WmsError('请填写物料编码')
        mode = request.get('box_mode', 'NEW')
        if kind == 'INBOUND':
            details = self.store.register_material(state, request)
            if mode == 'NEW':
                if request.get('box_barcode'):
                    raise WmsError('新箱由系统分配在库空箱，无需填写箱号')
                boxes = [box for box in state['stock'].values() if box.get('container_type') == 'EMPTY_BIN'
                         and box['quantity'] == 0 and not box['sku'] and self.available(state, box)]
                boxes.sort(key=lambda box: allocation_key(state, state['locations'][box['location']], inbound=False) + (box['barcode'],))
                if not boxes:
                    raise WmsError('没有可用的在库空箱，请先盘点登记或完成空箱入库')
                box = boxes[0]
            elif mode == 'EXISTING':
                box = state['stock'].get(request.get('box_barcode'))
                if not box or box.get('container_type', 'MATERIAL') != 'MATERIAL' or box['sku'] != sku:
                    raise WmsError('所选料箱不属于该物料编码，请重新选择')
                if material_details(box) != details:
                    raise WmsError('所选料箱的物料名称、型号或规格与档案不一致，请先核对资料')
            else:
                raise WmsError('请选择新箱或已有物料箱')
            batch = request.get('batch', '')
            if mode == 'EXISTING' and batch and batch != box.get('batch', ''):
                raise WmsError('已有料箱批次不同，请选择新箱')
            batch = box.get('batch', '') if mode == 'EXISTING' else batch
        else:
            box = state['stock'].get(request.get('box_barcode'))
            if not box or box.get('container_type', 'MATERIAL') != 'MATERIAL' or box['sku'] != sku:
                raise WmsError('请选择该物料的在库料箱')
            details, batch = material_details(box), box.get('batch', '')
            mode = 'EXISTING'
        if not self.available(state, box):
            raise WmsError('料箱已预留、库位停用或通道不可用，请重新选择')
        before = box['quantity']
        after = before + quantity if kind == 'INBOUND' else before - quantity
        if not 0 <= after <= 1000000:
            raise WmsError('出库数量超过箱内库存或入库后数量超过上限')
        state['order_sequence'] = state.get('order_sequence', 0) + 1
        order = dict(id=str(uuid.uuid4()), request_id=request['request_id'], request=copy.deepcopy(request),
            number=f'{"RK" if kind == "INBOUND" else "CK"}-{state["order_sequence"]:06}',
            kind=kind, sku=sku, **details, batch=batch, quantity=quantity, box_mode=mode,
            barcode=box['barcode'], location=box['location'], before_quantity=before, after_quantity=after,
            original_sku=box['sku'], status='QUEUED', quantity_posted=False, created_at=now(), updated_at=now())
        fetch = self.store._create_task(state, dict(request_id=request['request_id'], kind='OUTBOUND',
            barcode=box['barcode'], container_type=box.get('container_type', 'MATERIAL')))
        fetch.update(order_id=order['id'], order_leg='FETCH', document_kind=kind)
        order.update(fetch_task_id=fetch['id'], current_task_id=fetch['id'])
        state.setdefault('orders', []).insert(0, order)
        if kind == 'INBOUND' and mode == 'NEW':
            record_allocation(state, state['locations'][box['location']])
        self.store.event(state, f'{order["number"]} 已分配料箱 {box["barcode"]}；数量 {before} → {after}，待取箱', fetch['id'])
        return order

    def prepare_return(self, ident, request):
        def apply(state):
            order = self.order(state, ident)
            if order['status'] not in {'WAIT_LOAD', 'WAIT_PICK'}:
                raise WmsError('单据尚未到人工装料或取料阶段，或已提交返库')
            if not request['confirmed']:
                raise WmsError('请确认已经按单据数量装料或取料，并已核对现场')
            if order['kind'] == 'OUTBOUND':
                if request.get('scanned_barcode', '').strip() != order['barcode']:
                    raise WmsError('扫描箱号与本次出库料箱不一致')
                if request.get('scanned_sku', '').strip() != order['sku']:
                    raise WmsError('扫描物料编码与出库单据不一致')
            box = state['stock'][order['barcode']]
            fetch = self.store.task(state, order['fetch_task_id'])
            location = state['locations'][order['location']]
            if box['status'] != 'AT_STATION' or box.get('reserved') != fetch['id'] or fetch['status'] != 'AWAIT_RETURN':
                raise WmsError('交接点料箱或任务状态已变化，请重新核对')
            if not location['enabled'] or location['reserved'] != fetch['id']:
                raise WmsError('原库位预留已变化，不能返库')
            if box['quantity'] != order['before_quantity'] or box['sku'] != order['original_sku']:
                raise WmsError('箱内账面物料或数量已变化，不能按原单据返库')
            reason = self.store.access_error(state, location, inbound=True, task_id=fetch['id'])
            if reason:
                raise WmsError(reason)
            state['sequence'] += 1
            task = dict(id=str(uuid.uuid4()), number=f'WMS-{state["sequence"]:06}', request=copy.deepcopy(request),
                kind='INBOUND', order_id=ident, order_leg='RETURN', document_kind=order['kind'],
                barcode=order['barcode'], sku=order['sku'], quantity=order['after_quantity'], batch=order['batch'],
                container_type='MATERIAL' if order['after_quantity'] else 'EMPTY_BIN', **material_details(order),
                location=order['location'], dock=self.store.config['inbound_dock'], status='QUEUED', plc_id=None,
                detail='已确认人工操作，等待返库下发', created_at=now(), updated_at=now(), posted=False, ack_pending=False)
            fetch.update(status='COMPLETED', updated_at=now(), detail='交接点人工操作已核对，转入返库任务')
            state['tasks'].insert(0, task)
            location['reserved'] = box['reserved'] = task['id']
            order.update(status='RETURN_QUEUED', current_task_id=task['id'], return_task_id=task['id'],
                         station_confirmation=copy.deepcopy(request), updated_at=now())
            self.store.event(state, f'{order["number"]} 人工操作已确认；按单据返库后数量为 {order["after_quantity"]}', task['id'])
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
                if box['quantity'] != order['before_quantity'] or box['sku'] != order['original_sku']:
                    raise WmsError('返库数量过账前库存发生变化，请核对')
                if order['after_quantity']:
                    catalog = state['materials'].get(order['sku'], order)
                    box.update(container_type='MATERIAL', sku=order['sku'], quantity=order['after_quantity'],
                               batch=order['batch'], **material_details(catalog))
                else:
                    box.update(container_type='EMPTY_BIN', sku='', quantity=0, batch='', **material_details({}))
                state.setdefault('quantity_movements', []).insert(0, dict(order_id=order['id'],
                    number=order['number'], barcode=order['barcode'], sku=order['sku'], kind=order['kind'],
                    quantity=order['quantity'], delta=order['after_quantity']-order['before_quantity'],
                    before=order['before_quantity'], after=order['after_quantity'], time=now()))
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
            task.update(status='AWAIT_RETURN', detail='请按单据装料后返库' if order['kind'] == 'INBOUND' else '请扫码核对箱号及物料，按单据取料后返库')
            order['status'] = 'WAIT_LOAD' if order['kind'] == 'INBOUND' else 'WAIT_PICK'
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
            order.update(status='WAIT_LOAD' if order['kind'] == 'INBOUND' else 'WAIT_PICK', current_task_id=fetch['id'])
        order['updated_at'] = now()
        task.update(status='CANCELLED', updated_at=now(), detail='本次搬运已取消')
