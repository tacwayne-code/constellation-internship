"""A physical stock count retrieves a box and posts absolute counts on return only."""
import copy
import uuid
from .wms_store import WmsError, material_details, now
from .wms_contents import contents, set_contents


def create(store, request):
    def apply(state):
        box = state['stock'].get(request['box_barcode'])
        if not box or not store.orders.available(state, box):
            raise WmsError('请选择未预留、库位启用且通道可用的在库料箱')
        if box['updated_at'] != request['expected_updated_at']:
            raise WmsError('料箱库存已更新，请重新选择后创建盘点单')
        before = contents(box)
        first = before[0] if before else dict(sku='', quantity=0, batch='', **material_details({}))
        state['order_sequence'] = state.get('order_sequence', 0) + 1
        order = dict(id=str(uuid.uuid4()), request_id=request['request_id'], request=copy.deepcopy(request),
            number=f'PD-{state["order_sequence"]:06}', kind='COUNT', barcode=box['barcode'],
            location=box['location'], sku=first['sku'], quantity=box['quantity'], batch=first['batch'],
            **material_details(first), before_quantity=first['quantity'], after_quantity=first['quantity'],
            before_contents=before, after_contents=copy.deepcopy(before), original_sku=box['sku'],
            box_before_total=box['quantity'], box_after_total=box['quantity'], box_mode='EXISTING',
            lines=[dict(**line, before_quantity=line['quantity'], after_quantity=line['quantity']) for line in before],
            note=request['note'], status='QUEUED', quantity_posted=False, created_at=now(), updated_at=now())
        task = store._create_task(state, dict(request_id=request['request_id'], kind='OUTBOUND',
            barcode=box['barcode'], container_type=box.get('container_type', 'MATERIAL')))
        task.update(order_id=order['id'], order_leg='FETCH', document_kind='COUNT')
        order.update(fetch_task_id=task['id'], current_task_id=task['id'])
        state.setdefault('orders', []).insert(0, order)
        store.event(state, f'{order["number"]} 盘点单已创建，取箱出库后核对实际物料及数量，再返库记账', task['id'], created=True)
        return order
    return store.once(request['request_id'], {'operation':'count-order', **request}, apply)


def confirm(store, state, order, request):
    if 'items' not in request:
        raise WmsError('盘点单需核对实际物料明细后从盘点返库入口提交')
    rows = request['items']
    if len(rows) > 50 or len({row['sku'] for row in rows}) != len(rows):
        raise WmsError('盘点明细最多 50 种物料，同一编码不能重复')
    if not request.get('note', '').strip():
        raise WmsError('请填写盘点核对说明')
    after = []
    for row in rows:
        if not row.get('sku', '').strip() or type(row['quantity']) is not int or not 0 <= row['quantity'] <= 1000000:
            raise WmsError('物料编码不能为空，实盘数量须为 0 至 1000000 的整数')
        if row['quantity']:
            catalog = state.get('materials', {}).get(row['sku'])
            if catalog and any(value and value != catalog.get(key, '') for key, value in material_details(row).items()):
                raise WmsError('已有物料编码的资料需在物料档案中修订，盘点仅核对数量及批次')
            details = material_details(catalog) if catalog else store.register_material(state, row)
            after.append(dict(sku=row['sku'], quantity=row['quantity'], batch=row.get('batch', ''), **details))
    projected = {}
    set_contents(projected, after)
    before = {row['sku']:row for row in order['before_contents']}
    counted = {row['sku']:row for row in after}
    lines = []
    for sku in dict.fromkeys([*before, *counted]):
        row = counted.get(sku, before.get(sku))
        lines.append(dict(sku=sku, **material_details(row), batch=row.get('batch', ''),
            quantity=counted.get(sku, {}).get('quantity', 0),
            before_quantity=before.get(sku, {}).get('quantity', 0),
            after_quantity=counted.get(sku, {}).get('quantity', 0)))
    order.update(after_contents=after, lines=lines, box_after_total=projected['quantity'],
                 quantity=projected['quantity'], after_quantity=counted.get(order['sku'], {}).get('quantity', 0),
                 count_note=request['note'], count_confirmed_at=now())
    from .wms_actor import current_actor
    if current_actor.get():
        order['counted_by'] = dict(current_actor.get())
