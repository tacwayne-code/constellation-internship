"""Operator-started, persistent eight-leg aging cycle. Never replay after restart."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

from .wms_store import ACTIVE, WmsError, now
from .wms_service import feedback
from . import wms_transfer

PAIRS = [('L-01-01-1', 'R-15-06-1'), ('L-15-01-1', 'R-01-06-1'),
         ('R-01-01-1', 'L-15-06-1'), ('R-15-01-1', 'L-01-06-1')]
PLAN = [dict(group=index + 1, direction=direction, source=source, target=target)
        for index, (home, away) in enumerate(PAIRS)
        for direction, source, target in [('OUT', home, away), ('BACK', away, home)]]


def preparation(state):
    reasons, groups = [], []
    active = [task for task in state['tasks'] if task['status'] in ACTIVE]
    if active:
        reasons.append('当前设备有未结束任务，请先完成或取消')
    for index, (source, target) in enumerate(PAIRS, 1):
        group = dict(group=index, source=source, target=target, ready=False, reason='')
        try:
            check_coordinates(state, source, target)
            group['ready'] = True
        except WmsError as error:
            group['reason'] = str(error)
            reasons.append(f'第 {index} 组：{error}')
        groups.append(group)
    # Production enablement, stock, verification and reservations do not gate fixed-coordinate aging.
    coordinates = {key: {field: state['locations'][key].get(field) for field in
        ('side', 'level', 'column', 'depth')} if key in state['locations'] else None
        for pair in PAIRS for key in pair}
    token = hashlib.sha256(json.dumps(dict(locations=coordinates,
        tasks=[dict(id=task['id'], status=task['status']) for task in active]), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(ready=not reasons, reasons=reasons, groups=groups, token=token)


def check_coordinates(state, source, target):
    for key in (source, target):
        location = state['locations'].get(key)
        if not location:
            raise WmsError('测试坐标需已建档')
        side, column, level, depth = key.split('-')
        expected = (1 if side == 'L' else 2, int(level), int(column), int(depth))
        if tuple(location.get(field) for field in ('side', 'level', 'column', 'depth')) != expected:
            raise WmsError('测试库位坐标与固定路线不一致')


def create_coordinate_task(store, run, step):
    request = dict(request_id=f'aging-{run["id"]}-{run["completed_moves"]}',
                   source_location=step['source'], target_location=step['target'])
    def apply(state):
        current = state.get('aging_run')
        if not current or current['id'] != run['id'] or current['status'] != 'RUNNING' or current['current_task_id']:
            raise WmsError('老化运行状态已变化，禁止创建下一步')
        if current['completed_moves'] != run['completed_moves']:
            raise WmsError('老化步骤已变化')
        if any(task['status'] in ACTIVE for task in state['tasks']):
            raise WmsError('设备已有未结束任务')
        check_coordinates(state, step['source'], step['target'])
        state['sequence'] += 1
        task = dict(id=str(uuid.uuid4()), number=f'WMS-{state["sequence"]:06}', kind='AGING',
                    request=request, aging_run_id=run['id'], barcode='', sku='', quantity=0, batch='',
                    location=step['source'], target_location=step['target'], dock=store.config['outbound_dock'],
                    status='QUEUED', plc_id=None, posted=False, ack_pending=False,
                    detail='坐标老化步骤已登记，不关联库存', created_at=now(), updated_at=now())
        state['tasks'].insert(0, task)
        if run.get('actor'):
            actor = dict(id=run['actor_id'], username=run['actor'], display_name=run.get('actor_display_name', run['actor']))
            task.update(created_by=actor, operator=dict(actor))
        current.update(current_task_id=task['id'], current_started_at=now(), updated_at=now())
        store.event(state, f'{task["number"]} 坐标老化：{step["source"]} → {step["target"]}', task['id'])
        return task
    return store.once(request['request_id'], {'operation':'aging-coordinate', 'aging_run_id':run['id'], **request},
                      apply, aging_id=run['id'])


def snapshot(state):
    return dict(plan=copy.deepcopy(PLAN), preparation=preparation(state), run=copy.deepcopy(state.get('aging_run')))


class AgingRunner:
    def __init__(self, service, users):
        self.service, self.store, self.users = service, service.store, users

    def start(self, request, actor):
        service = self.service
        with service.lock, service.reader.lock:
            def apply(state):
                if not request['site_ready']:
                    raise WmsError('请核对固定坐标、动作区域及现场控制权')
                if not service.enabled or not service.auto_acknowledge:
                    raise WmsError('老化需要设备控制及自动回执确认已启用')
                if not actor['enabled'] or not {'inventory', 'control'}.issubset(actor['permissions']):
                    raise WmsError('启动老化需要库存调整及设备控制权限')
                prepared = preparation(state)
                if prepared['token'] != request['expected_token']:
                    raise WmsError('测试坐标或任务已变化，请重新核对后开始')
                if not prepared['ready']:
                    raise WmsError('；'.join(prepared['reasons']))
                service.reader.poll()
                ready = service.readiness()
                if not ready['ready']:
                    raise WmsError('；'.join(ready['reasons']))
                run = dict(id=uuid.uuid4().hex, status='RUNNING', next_step=0, completed_moves=0, completed_cycles=0,
                           limit_cycles=request['limit_cycles'], step_timeout=request['step_timeout'],
                           mode='COORDINATE_ONLY', actor_id=actor['id'],
                           actor=actor['username'], actor_display_name=actor['display_name'], started_at=now(), updated_at=now(), ended_at=None,
                           current_task_id=None, current_started_at=None, stop_requested=False,
                           detail='老化已启动，准备第 1 步；四组往返共八步一圈', last_error='')
                state['aging_run'] = run
                self.store.event(state, f'老化测试启动：{actor["username"]}，目标 {request["limit_cycles"] or "持续"} 圈')
                return run
            self.store.once(request['request_id'], {'operation':'aging-start', **request}, apply)
            return snapshot(self.store.read())

    def pause(self, reason, stop_requested=False):
        def apply(state):
            run = state.get('aging_run')
            if not run or run['status'] == 'STOPPED':
                return
            if run['status'] == 'RUNNING' or stop_requested:
                run.update(status='PAUSED', last_error=reason, detail=reason, updated_at=now(),
                           stop_requested=run['stop_requested'] or stop_requested)
                self.store.event(state, '老化已暂停：' + reason)
        self.store.mutate(apply)

    def request_stop(self, request):
        with self.service.lock:
            current = self.store.read().get('aging_run')
            if not current or current['id'] != request['run_id']:
                raise WmsError('老化批次已变化，请刷新后重试')
            def apply(state):
                run = state.get('aging_run')
                if not run or run['id'] != request['run_id']:
                    raise WmsError('老化批次已变化，请刷新后重试')
                if run['status'] != 'STOPPED':
                    run.update(status='STOPPING', stop_requested=True, detail='已停止后续下发，等待当前搬运完成或现场核对', updated_at=now())
                    self.store.event(state, '老化已请求停止循环，不再下发下一步')
                return run['id']
            self.store.once(request['request_id'], {'operation':'aging-stop', **request}, apply, aging_id=request['run_id'])
            self.tick()
            return snapshot(self.store.read())

    def _finish(self, detail, error=''):
        def apply(state):
            run = state['aging_run']
            run.update(status='STOPPED', ended_at=now(), updated_at=now(), detail=detail, last_error=error)
            history = state.setdefault('aging_history', [])
            history.insert(0, copy.deepcopy(run))
            del history[50:]
            self.store.event(state, f'老化已结束：{detail}；完成 {run["completed_cycles"]} 圈 / {run["completed_moves"]} 步')
        self.store.mutate(apply)

    def _authorized(self, run):
        with self.users.db() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (run['actor_id'],)).fetchone()
        return bool(row and row['enabled'] and {'inventory', 'control'}.issubset(self.users.public(row)['permissions']))

    def tick(self):
        # Called under the service lock. One accepted command per tick at most.
        state = self.store.read()
        run = state.get('aging_run')
        if not run or run['status'] == 'STOPPED':
            return
        task = self.store.task(state, run['current_task_id']) if run['current_task_id'] else None
        if task and task['status'] == 'COMPLETED':
            if task.get('aging_resolution') or not task.get('ack_source'):
                self._finish('当前搬运经人工处理，老化已结束；该步不计入完成圈数', '请现场核对后重新开始')
                return
            def completed(state):
                current = state['aging_run']
                current['completed_moves'] += 1
                current['completed_cycles'] = current['completed_moves'] // 8
                current['next_step'] = current['completed_moves'] % 8
                current.update(current_task_id=None, current_started_at=None, updated_at=now(),
                               detail=f'已完成 {current["completed_cycles"]} 圈，' + (f'准备第 {current["next_step"] + 1} 步' if current['status'] == 'RUNNING' else '后续下发保持暂停'))
            self.store.mutate(completed)
            run = self.store.read()['aging_run']
            if run['stop_requested'] or (run['limit_cycles'] and run['completed_cycles'] >= run['limit_cycles']):
                self._finish('停止循环，当前搬运已完成' if run['stop_requested'] else '已达到目标圈数，八步往返已完成')
            return
        if task and task['status'] == 'CANCELLED':
            self._finish('当前搬运已取消，老化已结束')
            return
        if run['stop_requested'] or run['status'] == 'STOPPING':
            if not task:
                self._finish('已停止循环，没有执行中的搬运')
            elif task['status'] == 'QUEUED':
                self.store.cancel(task['id'], aging_id=run['id'])
                self._finish('已停止循环，未下发步骤已取消')
            return
        if run['status'] != 'RUNNING':
            return
        if not self._authorized(run):
            self.pause('启动用户已停用或权限已变更，老化后续下发已暂停')
            return
        if not self.service.enabled or not self.service.auto_acknowledge:
            self.pause('设备控制或自动回执确认已关闭，老化已暂停')
            return
        if task:
            sample = self.service.reader.snapshot()
            fields = feedback(sample)
            if sample.get('cpu_state') != 'S7CpuStatusRun' or fields.get('DB2.DBW4') != 0 or fields.get('DB2.DBW6') != 1:
                self.pause('CPU、报警或远程任务许可已变化，老化后续下发已暂停')
                return
            if task.get('plc_id') and sample.get('command_task_id') != task['plc_id']:
                self.pause('PLC 当前任务号与老化步骤不匹配，已暂停并保留任务；请现场核对')
                return
            if task['status'] == 'REVIEW' or task.get('ack_error'):
                self.pause('当前步骤需现场核对或回执确认异常，老化已暂停：' + task['detail'])
            elif task['status'] == 'QUEUED':
                self.pause('步骤已登记但未完成下发，禁止自动重发；请停止循环并重新核对')
            elif (datetime.now(timezone.utc) - datetime.fromisoformat(run['current_started_at'])).total_seconds() > run['step_timeout']:
                self.pause('当前步骤超时，已暂停后续下发；请核对设备和箱位，必要时请求设备停止')
            return
        readiness = self.service.readiness()
        if not readiness['ready']:
            self.pause('设备接单条件不满足，老化已暂停：' + '；'.join(readiness['reasons']))
            return
        try:
            step = PLAN[run['next_step']]
            if run.get('mode') == 'COORDINATE_ONLY':
                task = create_coordinate_task(self.store, run, step)
            else:  # Legacy inventory-linked runs retain their original bookkeeping.
                barcode = run['barcodes'][step['group'] - 1]
                box = state['stock'].get(barcode)
                if not box:
                    raise WmsError('测试料箱记录已变化')
                request = dict(request_id=f'aging-{run["id"]}-{run["completed_moves"]}', barcode=barcode,
                               source_location=step['source'], target_location=step['target'], expected_updated_at=box['updated_at'])
                task = wms_transfer.create(self.store, request, aging_id=run['id'])
            sent = self.service.dispatch(task['id'], True, aging_id=run['id'])
            if sent['status'] == 'REVIEW':
                self.pause('下发结果待核对，老化已暂停，不自动重发：' + sent['detail'])
            else:
                self.store.mutate(lambda state: state['aging_run'].update(
                    detail=f'第 {run["next_step"] + 1} 步已提交，等待设备完成及回执确认', updated_at=now()))
        except Exception as error:
            self.pause('老化步骤未正常下发，不自动重发：' + str(error))
