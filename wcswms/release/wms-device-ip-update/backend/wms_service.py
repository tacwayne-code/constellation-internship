"""Real S7 task dispatch, correlated completion and durable acknowledgement."""
import copy
import logging
import struct
import threading
from time import monotonic

from fastapi import HTTPException
from .physical_app import PhysicalReader
from .physical_control import Command, ManualControl
from .wms_store import WmsError, IN_FLIGHT, now


class WmsReader(PhysicalReader):
    def __init__(self, config):
        super().__init__(config)
        self.data.update(mode='PHYSICAL_WMS', task_dispatch_enabled=config.get('control_enabled') is True)
        self._publish_snapshot()

    def replace_connection(self, replacement, config):
        """Invalidate the entire old device sample while retaining this reader/lock."""
        with self.lock:
            try:
                self.close()
            except Exception:
                logging.getLogger(__name__).exception('Old PLC client cleanup failed during IP change')
            finally:
                self.client = None
            self.config = config
            self.data = replacement.data
            self.last_success = self.axis_last_success = 0
            self.axis_baseline = None
            self.history = replacement.history
            self._publish_snapshot()

    def _publish_snapshot(self):
        # Called under the reentrant PLC lock (or before the reader is shared).
        # Replace one complete, detached sample; HTTP readers never see partial I/O.
        self._published_snapshot = (super().snapshot(), self.last_success, self.axis_last_success)

    def snapshot(self):
        # A disconnected PLC can spend seconds connecting. Keep the WMS HTTP
        # endpoints responsive while retaining the last complete sample.
        # Control operations already hold this RLock and still obtain fresh data.
        if self.lock.acquire(blocking=False):
            try:
                self._publish_snapshot()
            finally:
                self.lock.release()
        published, last_success, axis_last_success = self._published_snapshot
        result = copy.deepcopy(published)
        now = monotonic()
        age = now - last_success if last_success else None
        axis_age = now - axis_last_success if axis_last_success else None
        result['age_seconds'] = round(age, 2) if age is not None else None
        result['axis_age_seconds'] = round(axis_age, 2) if axis_age is not None else None
        result['live'] = bool(result['connected'] and age is not None and age < 5)
        result['axis_live'] = bool(result['live'] and result['axis_connected'] and axis_age is not None and axis_age < 5)
        return result

    def poll(self):
        with self.lock:
            try:
                super().poll()
                if not self.client or not self.data['connected']:
                    self.data['connected'] = False
                    return
                try:
                    words = struct.unpack('>3h', bytes(self.client.db_read(2, 28, 6)))
                    self.data.update(command_code=words[0], command_task_id=words[1], idle_ack=words[2])
                except Exception as error:
                    self.data.update(connected=False, last_error=str(error))
                    self.close()
            finally:
                self._publish_snapshot()


def feedback(snapshot):
    return {item['address']: item['value'] for item in snapshot.get('signals', [])}


class WmsService:
    def __init__(self, store, reader, commands_path, enabled=True, auto_acknowledge=True):
        self.store, self.reader = store, reader
        self.enabled = enabled
        self.auto_acknowledge = bool(enabled and auto_acknowledge)
        self.lock = threading.RLock()
        self.control = ManualControl(reader, commands_path) if enabled else None

    def sync_order_phase(self, state, task):
        if not task.get('order_id'):
            return
        if task['status'] == 'REVIEW':
            phase = 'REVIEW'
        elif task['status'] == 'QUEUED':
            phase = 'QUEUED' if task['order_leg'] == 'FETCH' else 'RETURN_QUEUED'
        else:
            phase = 'FETCHING' if task['order_leg'] == 'FETCH' else 'RETURNING'
        self.store.orders.order(state, task['order_id']).update(status=phase, updated_at=now())

    def readiness(self):
        snapshot = self.reader.snapshot()
        fields = feedback(snapshot)
        reasons = []
        if not self.enabled:
            reasons.append('后台写入未启用')
        if not snapshot.get('live'):
            reasons.append('PLC 反馈未就绪或已过期')
            # Unknown/stale values cannot establish CPU mode or an active alarm.
            return dict(ready=False, reasons=reasons, write_enabled=self.enabled)
        if snapshot.get('cpu_state') != 'S7CpuStatusRun':
            reasons.append('CPU 未处于 RUN')
        if fields.get('DB2.DBW0') != 8:
            reasons.append(f'PLC 状态={fields.get("DB2.DBW0", "未知")}，接单需空闲 8')
        if fields.get('DB2.DBW4') != 0:
            reasons.append('PLC 有报警')
        if fields.get('DB2.DBW6') != 1:
            reasons.append('远程任务许可未开启，请在现场 HMI 核对远程模式和任务执行许可')
        if snapshot.get('command_code', 0) != 0:
            reasons.append('PLC 命令码尚未清零')
        if snapshot.get('idle_ack', 0) != 0:
            reasons.append('完成确认尚未复位，请先确认 PLC 回执')
        return dict(ready=not reasons, reasons=reasons, write_enabled=self.enabled)

    def poll(self):
        with self.lock:
            self.reader.poll()
            snapshot = self.reader.snapshot()
            if not snapshot.get('live'):
                return
            fields = feedback(snapshot)
            status, ident, alarm = (fields.get(key) for key in ('DB2.DBW0', 'DB2.DBW2', 'DB2.DBW4'))
            def update(state):
                for task in state['tasks']:
                    if task['status'] not in {'DISPATCHING', 'SENT', 'RUNNING', 'REVIEW'} or not task['plc_id']:
                        continue
                    matching = snapshot.get('command_task_id') == task['plc_id']
                    expected = 2 if task['kind'] == 'INBOUND' else 1
                    if matching and ident == task['plc_id'] and status == expected and not alarm and snapshot['cpu_state'] == 'S7CpuStatusRun':
                        self.store.post_completion(state, task)
                    elif matching and status == 9 and task['status'] == 'SENT':
                        task.update(status='RUNNING', detail='PLC 任务号匹配，设备忙线', updated_at=now())
                        self.store.event(state, f'{task["number"]} PLC 进入执行状态', task['id'])
                    elif (status == 7 or alarm) and task['status'] != 'REVIEW':
                        task.update(status='REVIEW', detail=f'PLC 报警 {alarm}，保留库位与料箱预留', updated_at=now())
                        self.sync_order_phase(state, task)
                        self.store.event(state, task['detail'], task['id'])
            self.store.mutate(update)
            if self.auto_acknowledge:
                self._auto_acknowledge()

    def _auto_acknowledge(self):
        # The service lock serializes polling and operator actions. Persist the
        # intent before I/O; interrupted acknowledgements must not emit a second
        # assertion automatically after a restart.
        for task in self.store.read()['tasks']:
            if task['status'] != 'PLC_DONE' or not task['posted'] or not task['ack_pending'] or task.get('ack_error'):
                continue
            resumed = bool(task.get('auto_ack_started_at'))
            if not resumed:
                self.store.mutate(lambda state: self.store.task(state, task['id']).update(
                    auto_ack_started_at=now(), detail='完成回执已匹配，正在自动确认 PLC 回执'))
            try:
                with self.reader.lock:
                    self._acknowledge(task['id'], automatic=True, allow_signal=not resumed)
            except Exception as error:
                def failed(state):
                    item = self.store.task(state, task['id'])
                    item.update(ack_error=str(error), updated_at=now(),
                        detail=f'自动回执确认暂停：{error}；请现场核对后重试回执确认')
                    self.store.event(state, f'{item["number"]} {item["detail"]}', item['id'])
                self.store.mutate(failed)

    def dispatch(self, ident, site_ready):
        if not site_ready:
            raise WmsError('请确认入口料箱、出口空闲和现场控制权')
        if not self.enabled:
            raise WmsError('后台未启用写入')
        with self.lock, self.reader.lock:
            state = self.store.read()
            task = self.store.task(state, ident)
            if task['status'] != 'QUEUED':
                return task  # An HTTP retry must never resend motion commands.
            if any(other['id'] != ident and other['status'] in IN_FLIGHT for other in state['tasks']):
                raise WmsError('设备已有未结束任务或待确认回执')
            self.check_dispatch(state, task)
            self.reader.poll()
            ready = self.readiness()
            if not ready['ready']:
                raise WmsError('；'.join(ready['reasons']))
            with self.control.db() as db:
                used = {row[0] for row in db.execute('SELECT task_id FROM commands WHERE task_id IS NOT NULL')}
            snapshot = self.reader.snapshot()
            used.update([snapshot.get('command_task_id'), feedback(snapshot).get('DB2.DBW2')])
            def prepare(state):
                item = self.store.task(state, ident)
                if not state['locations'][item['location']]['enabled']:
                    raise WmsError('任务库位已停用，禁止下发；请核对库位与任务')
                self.check_dispatch(state, item)
                plc_id = item['plc_id'] or state['next_plc_id']
                while plc_id in used:
                    plc_id += 1
                if plc_id > 32767:
                    raise WmsError('PLC 任务号已耗尽，需受控归档后重新分配')
                state['next_plc_id'] = max(state['next_plc_id'], plc_id + 1)
                item.update(plc_id=plc_id, status='DISPATCHING', detail='已保存下发意图，正在写入 PLC', updated_at=now())
                if item.get('order_id'):
                    self.store.orders.order(state, item['order_id']).update(
                        status='FETCHING' if item['order_leg'] == 'FETCH' else 'RETURNING', updated_at=now())
                return item
            task = self.store.mutate(prepare)
            location = state['locations'][task['location']]
            params = dict(request_id=task['id'], action='task', task_id=task['plc_id'],
                          function=2 if task['kind'] == 'INBOUND' else 1, dock=task['dock'], site_ready=True)
            for prefix in ('source', 'target'):
                use_location = (prefix == 'source') == (task['kind'] == 'OUTBOUND')
                for field in ('side', 'level', 'column', 'depth'):
                    params[f'{prefix}_{field}'] = location[field] if use_location else 0
            try:
                result = self.control.execute(Command(**params))
            except HTTPException as error:
                with self.control.db() as db:
                    intent = db.execute('SELECT 1 FROM commands WHERE id=?', (ident,)).fetchone()
                def failed(state):
                    item = self.store.task(state, ident)
                    item.update(status='REVIEW' if intent else 'QUEUED', detail=str(error.detail), updated_at=now())
                    self.sync_order_phase(state, item)
                    return item
                self.store.mutate(failed)
                raise WmsError(str(error.detail)) from error
            except Exception as error:
                def uncertain(state):
                    item = self.store.task(state, ident)
                    item.update(status='REVIEW', detail=f'下发结果待核对：{error}', updated_at=now())
                    self.sync_order_phase(state, item)
                self.store.mutate(uncertain)
                raise WmsError('下发结果待核对，禁止自动重发') from error
            def record(state):
                item = self.store.task(state, ident)
                item.update(status='SENT' if result['status'] == 'SENT' else 'REVIEW', detail=result['detail'], updated_at=now(), command_result=result)
                self.sync_order_phase(state, item)
                self.store.event(state, f'{item["number"]} 下发结果：{result["status"]}', ident)
                return item
            return self.store.mutate(record)

    def check_dispatch(self, state, task):
        station = next((box for box in state['stock'].values() if box['status'] == 'AT_STATION'), None)
        if station and not (task.get('order_leg') == 'RETURN' and station.get('reserved') == task['id']):
            raise WmsError('交接点正在装料或取料，请先完成该箱返库')
        location = state['locations'][task['location']]
        if not location['enabled']:
            raise WmsError('任务库位已停用，禁止下发')
        if task['kind'] == 'OUTBOUND' and any(item['status'] == 'AT_EXIT' for item in state['stock'].values()):
            raise WmsError('出口尚有待取料箱，请先扫码交接，再下发下一条出库')
        reason = self.store.access_error(state, location, inbound=task['kind'] == 'INBOUND', task_id=task['id'])
        if reason:
            raise WmsError(reason)
        if task.get('order_id'):
            order = self.store.orders.order(state, task['order_id'])
            box = state['stock'][order['barcode']]
            expected = 'IN_STOCK' if task['order_leg'] == 'FETCH' else 'AT_STATION'
            if order['current_task_id'] != task['id'] or box['status'] != expected or box.get('reserved') != task['id'] or location['reserved'] != task['id']:
                raise WmsError('料箱、原库位或单据步骤已变化，禁止下发')
            if box['quantity'] != order['before_quantity'] or box['sku'] != order['original_sku']:
                raise WmsError('箱内物料或数量已变化，禁止按原单据下发')

    def return_order(self, ident, request):
        if not request['confirmed'] or not self.enabled:
            raise WmsError('请核对人工操作和现场控制权')
        with self.lock, self.reader.lock:
            task = self.store.orders.prepare_return(ident, request)
            return self.dispatch(task['id'], True)

    def acknowledge(self, ident, site_ready):
        if not site_ready or not self.enabled:
            raise WmsError('请核对设备已经完成及 PLC 控制权')
        with self.lock, self.reader.lock:
            return self._acknowledge(ident)

    def _acknowledge(self, ident, automatic=False, allow_signal=True):
        task = self.store.task(self.store.read(), ident)
        if task['status'] in {'COMPLETED', 'AWAIT_PICKUP', 'AWAIT_RETURN'}:
            return task
        if task['status'] != 'PLC_DONE' or not task['posted']:
            raise WmsError('任务尚无匹配的完成回执')
        self._ack_plc(task['plc_id'], 2 if task['kind'] == 'INBOUND' else 1, allow_signal=allow_signal)
        self.reader.poll()
        def finish(state):
            item = self.store.task(state, ident)
            item.update(status='COMPLETED' if item['kind'] == 'INBOUND' else 'AWAIT_PICKUP', ack_pending=False,
                ack_error=None, ack_source='AUTO' if automatic else 'MANUAL', acknowledged_at=now(),
                updated_at=now(), detail='入库完成' if item['kind'] == 'INBOUND' else '料箱在出口，请扫码确认取走')
            if item.get('order_id'):
                self.store.orders.acknowledged(state, item)
            self.store.event(state, f'{item["number"]} PLC 完成回执已{"自动" if automatic else "人工"}确认，确认信号已复位', ident)
            return item
        return self.store.mutate(finish)

    def _ack_plc(self, plc_id, expected, allow_signal=True):
        # Original FB network "任务完成清除指令": DBW32=1 acknowledges
        # completion. Clear it after idle is observed so the next receipt survives.
        import time
        self.reader.poll()
        snap = self.reader.snapshot()
        if not snap.get('live') or snap['cpu_state'] != 'S7CpuStatusRun' or not self.reader.client:
            raise WmsError('PLC 反馈不可用，未确认回执')
        raw = struct.unpack('>17h', bytes(self.reader.client.db_read(2, 0, 34)))
        if raw[2] != 0 or raw[3] != 1 or raw[15] != plc_id:
            raise WmsError('PLC 任务号、远程许可或报警条件不匹配，未确认回执')
        if raw[14] != 0 or raw[16] not in (0, 1):
            raise WmsError('PLC 有待执行命令或确认信号异常，未确认回执')
        if raw[0] == 8 and raw[16] == 0:
            return  # Previous ack completed, but HTTP/database result was lost.
        if raw[0] != 8 and (raw[0] != expected or raw[1] != plc_id):
            raise WmsError('PLC 完成状态或任务号已经改变')
        try:
            if raw[0] != 8 and raw[16] == 0:
                if not allow_signal:
                    raise WmsError('上次自动确认被中断，禁止自动重复发送确认信号')
                self.reader.client.db_write(2, 32, bytearray(struct.pack('>h', 1)))
            deadline = time.monotonic() + 2
            while True:
                raw = struct.unpack('>17h', bytes(self.reader.client.db_read(2, 0, 34)))
                if raw[15] != plc_id or raw[2] != 0 or raw[3] != 1 or raw[14] != 0:
                    raise WmsError('确认期间 PLC 任务、报警、远程许可或命令改变；保留待核对状态')
                if raw[0] != 8 and (raw[0] != expected or raw[1] != plc_id):
                    raise WmsError('确认期间 PLC 完成状态或任务号改变；保留待核对状态')
                if raw[0] == 8:
                    self.reader.client.db_write(2, 32, bytearray(b'\x00\x00'))
                    if bytes(self.reader.client.db_read(2, 32, 2)) != b'\x00\x00':
                        raise WmsError('完成确认位复位未读回')
                    return
                if time.monotonic() >= deadline:
                    raise WmsError('PLC 尚未回到空闲，保留待确认任务；不要重复下发')
                time.sleep(.05)
        except Exception as error:
            if isinstance(error, WmsError):
                raise
            raise WmsError(f'回执确认结果待核对：{error}') from error

    def stop(self, request_id, site_ready):
        if not self.enabled:
            raise WmsError('后台未启用写入')
        with self.lock:
            result = self.control.execute(Command(request_id=request_id, action='stop', site_ready=site_ready))
            def update(state):
                for task in state['tasks']:
                    if task['status'] in {'DISPATCHING', 'SENT', 'RUNNING'}:
                        task.update(status='REVIEW', detail='已请求 PLC 停止，需核对料箱实际位置', updated_at=now())
                        self.sync_order_phase(state, task)
                self.store.event(state, f'向 PLC 请求任务停止：{result["status"]}')
            self.store.mutate(update)
            return result

    def resolve(self, ident, outcome, note, site_ready):
        if not site_ready:
            raise WmsError('请确认已经在现场核对料箱位置')
        with self.lock:
            self.reader.poll()
            snap = self.reader.snapshot()
            if not snap.get('live') or feedback(snap).get('DB2.DBW0') == 9 or snap.get('command_code') != 0 or snap.get('idle_ack') != 0:
                raise WmsError('设备仍在执行、状态不可核对或 PLC 尚有待处理命令，不能解除预留')
            def update(state):
                task = self.store.task(state, ident)
                if task['status'] != 'REVIEW':
                    raise WmsError('仅待核对任务可以人工处理')
                if outcome == 'AT_DESTINATION':
                    self.store.post_completion(state, task, manual=True)
                    task.update(ack_pending=False, status='COMPLETED' if task['kind'] == 'INBOUND' else 'AWAIT_PICKUP')
                    if task.get('order_id'):
                        self.store.orders.acknowledged(state, task)
                else:
                    if task.get('order_id'):
                        self.store.orders.cancelled(state, task)
                    else:
                        self.store.release(state, task)
                        task['status'] = 'CANCELLED'
                task.update(detail=f'人工核对：{note}', updated_at=now())
                self.store.event(state, f'{task["number"]} 人工处理 {outcome}：{note}', ident)
                return task
            return self.store.mutate(update)
