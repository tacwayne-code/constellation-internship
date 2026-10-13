"""Atomic whole-box relocation within a single device's ledger."""
import uuid
from .wms_store import ACTIVE, WmsStore, WmsError, material_details, now, task_locations


def check(state, barcode, source, target, task=None):
    ident = task['id'] if task else None
    box = state['stock'].get(barcode)
    if not box or box['status'] != 'IN_STOCK' or box['location'] != source or box.get('reserved') not in (None, ident):
        raise WmsError('源料箱不在可调仓库存中，或已被其他任务预留')
    if any(t['id'] != ident and t['barcode'] == barcode and t['status'] in ACTIVE for t in state['tasks']):
        raise WmsError('该料箱已有未结束任务')
    if source == target:
        raise WmsError('原库位与目标库位不能相同')
    for key, label in ((source, '原'), (target, '目标')):
        location = state['locations'].get(key)
        if not location or not location['enabled'] or not location['verified']:
            raise WmsError(label + '库位需已建档、启用并核对')
        if location['reserved'] not in (None, ident) or any(t['id'] != ident and key in task_locations(t) and t['status'] in ACTIVE for t in state['tasks']):
            raise WmsError(label + '库位已有预留或未结束任务')
        if task and location['reserved'] != ident:
            raise WmsError(label + '库位预留已变化，不能继续调仓')
    if any(b['location'] == target and b['status'] == 'IN_STOCK' for b in state['stock'].values()):
        raise WmsError('目标库位已有库存，请选择空库位')
    if task:
        if box.get('reserved') != ident or any(box.get(key, 'MATERIAL' if key == 'container_type' else '') != task.get(key, '') for key in ('sku', 'quantity', 'batch', 'container_type', *material_details(box))):
            raise WmsError('源料箱预留、物料或数量已变化，不能按原任务调仓')
    # The box is retrieved before placing it. Ignore this move's own locks;
    # for target access only, its former front-slot occupancy is now cleared.
    context = {**state, 'tasks': [t for t in state['tasks'] if t['id'] != ident],
               'locations': {key: {**loc, 'reserved': None if ident and loc['reserved'] == ident else loc['reserved']}
                             for key, loc in state['locations'].items()}}
    error = WmsStore.access_error(context, state['locations'][source])
    if error:
        raise WmsError('原库位通道：' + error)
    context['stock'] = {key: value for key, value in state['stock'].items() if key != barcode}
    error = WmsStore.access_error(context, state['locations'][target], inbound=True)
    if error:
        raise WmsError('目标库位通道：' + error)
    return box


def create(store, request):
    def apply(state):
        box = check(state, request['barcode'], request['source_location'], request['target_location'])
        if box['updated_at'] != request['expected_updated_at']:
            raise WmsError('源料箱信息已变化，请重新选择并核对')
        state['sequence'] += 1
        task = dict(id=str(uuid.uuid4()), number=f'WMS-{state["sequence"]:06}', kind='MOVE', request=request,
                    barcode=box['barcode'], sku=box['sku'], quantity=box['quantity'], batch=box['batch'],
                    container_type=box.get('container_type', 'MATERIAL'), **material_details(box),
                    location=box['location'], target_location=request['target_location'],
                    dock=store.config['outbound_dock'], status='QUEUED', plc_id=None,
                    detail='调仓已登记，等待核对原库位、目标库位并下发', posted=False, ack_pending=False,
                    created_at=now(), updated_at=now())
        state['tasks'].insert(0, task)
        for key in task_locations(task):
            state['locations'][key]['reserved'] = task['id']
        box['reserved'] = task['id']
        store.event(state, f'{task["number"]} 调仓已登记：{box["barcode"]} / {box["location"]} → {request["target_location"]}', task['id'])
        return task
    return store.once(request['request_id'], {'operation': 'transfer', **request}, apply)
