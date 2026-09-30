"""Deterministic single-crane simulator. No PLC connections or real Odoo writes.

Every transition (including physical inventory and outbox) commits atomically.
SQLite snapshot storage is deliberately limited to the local, single-process MVP.
"""
import copy
import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class DomainError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Engine:
    def __init__(self, path, layout, recover=True):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.layout = layout
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS warehouse (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM warehouse').fetchone():
                db.execute('INSERT INTO warehouse VALUES (1, ?)', (json.dumps(self.seed(), ensure_ascii=False),))
        if recover:
            self.mutate(self.recover)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def mutate(self, fn):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT payload FROM warehouse WHERE id=1').fetchone()[0])
            result = fn(state)
            self.validate(state)
            db.execute('UPDATE warehouse SET payload=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
            return copy.deepcopy(result)

    def read(self):
        with self.lock, self.connect() as db:
            return json.loads(db.execute('SELECT payload FROM warehouse WHERE id=1').fetchone()[0])

    def event(self, s, message, level='info', task_id=None):
        s['events'].insert(0, dict(id=str(uuid.uuid4()), time=now(), message=message, level=level, task_id=task_id))
        s['events'] = s['events'][:1000]

    def seed(self):
        locations = {}
        for side in self.layout['sides']:
            for col in range(1, self.layout['columns'] + 1):
                for level in range(1, self.layout['levels'] + 1):
                    key = f'{side}-{col:02}-{level:02}'
                    locations[key] = dict(id=key, side=side, column=col, level=level, load=None, reserved=None)
        s = dict(schema=1, mode='SIMULATION', layout=self.layout, locations=locations,
                 stations={'IN': dict(id='IN', load=None, reserved=None), 'OUT': dict(id='OUT', load=None, reserved=None)},
                 loads={}, tasks=[], events=[], outbox=[],
                 device=dict(id='SC-01', status='IDLE', paused=False, fault=None, speed=1,
                             x=0.0, y=0.4, z=0.0, carrying=None, active_task=None))
        for i, key in enumerate(['L-01-01','L-02-03','L-04-02','L-06-04','R-01-02','R-03-04','R-05-01','R-06-03']):
            if key not in locations:
                continue
            ident = f'BOX-{i+1:03}'
            s['loads'][ident] = dict(id=ident, sku=f'SKU-{i%3+1:03}', quantity=10, weight_kg=35, location=key)
            locations[key]['load'] = ident
        self.event(s, '仿真仓库已初始化；演示库存 8 个载具，未连接真实 PLC 或 Odoo。')
        return s

    def recover(self, s):
        if s['device']['active_task']:
            task = self.task(s, s['device']['active_task'])
            task['status'] = 'RECOVERY_REQUIRED'
            s['device'].update(paused=True, status='RECOVERY_REQUIRED')
            self.event(s, '服务重启：保留载荷和资源预留，请核对后恢复仿真。', 'warning', task['id'])

    @staticmethod
    def task(s, task_id):
        for task in s['tasks']:
            if task['id'] == task_id:
                return task
        raise DomainError('任务不存在')

    @staticmethod
    def place(s, key):
        if key in s['locations']:
            return s['locations'][key]
        if key in s['stations']:
            return s['stations'][key]
        raise DomainError('库位或站台不存在')

    def create(self, request):
        def apply(s):
            key = request['request_id']
            existing = next((t for t in s['tasks'] if t['request_id'] == key), None)
            if existing:
                if existing['request'] != request:
                    raise DomainError('相同请求编号不能用于不同任务')
                return existing
            kind = request['kind']
            load_id = request['load_id']
            target = request.get('target')
            if kind == 'INBOUND':
                source = 'IN'
                if load_id in s['loads']:
                    raise DomainError('载具编号已存在')
                if target == 'AUTO':
                    target = next((k for k, p in s['locations'].items() if not p['load'] and not p['reserved']), None)
                if target not in s['locations']:
                    raise DomainError('请选择有效的入库目标库位')
                if request['weight_kg'] > s['layout']['max_load_kg']:
                    raise DomainError('载荷超过仿真配置的重量上限')
            elif kind in ('OUTBOUND', 'TRANSFER'):
                load = s['loads'].get(load_id)
                if not load or load['location'] not in s['locations']:
                    raise DomainError('载具不在货架库位')
                source = load['location']
                target = 'OUT' if kind == 'OUTBOUND' else target
                if kind == 'TRANSFER' and target not in s['locations']:
                    raise DomainError('请选择有效的移库目标')
            else:
                raise DomainError('不支持的任务类型')
            if source == target:
                raise DomainError('源库位与目标库位不能相同')
            src, dst = self.place(s, source), self.place(s, target)
            if src['reserved'] or dst['reserved']:
                raise DomainError('源位置或目标位置已被其他任务预留')
            if dst['load'] or (kind == 'INBOUND' and src['load']):
                raise DomainError('目标位置或入库站台已有载具')
            ident = str(uuid.uuid4())
            if kind == 'INBOUND':
                s['loads'][load_id] = dict(id=load_id, sku=request['sku'], quantity=request['quantity'], weight_kg=request['weight_kg'], location='IN')
                src['load'] = load_id
            src['reserved'] = dst['reserved'] = ident
            t = dict(id=ident, number=f'T{len(s["tasks"])+1:05}', request_id=key, request=request,
                     kind=kind, load_id=load_id, source=source, target=target, status='QUEUED',
                     phase='MOVE_SOURCE', phase_time=0.0, created_at=now(), completed_at=None,
                     integration='NOT_SENT', plc_function={'INBOUND':2,'OUTBOUND':1,'TRANSFER':3}[kind])
            s['tasks'].append(t)
            self.event(s, f'{t["number"]} 已预留 {source} → {target}', task_id=ident)
            return t
        return self.mutate(apply)

    def coords(self, s, key):
        if key == 'IN':
            return 0.0, 0.4, -1.15
        if key == 'OUT':
            return 0.0, 0.4, 1.15
        p = s['locations'][key]
        return (p['column'] * s['layout']['column_pitch_m'],
                0.4 + (p['level']-1) * s['layout']['level_pitch_m'],
                (-1 if p['side'] == 'L' else 1) * s['layout']['aisle_half_width_m'])

    @staticmethod
    def approach(value, goal, amount):
        return value + max(-amount, min(amount, goal-value))

    def tick(self, seconds=0.1, force=False):
        def apply(s):
            d = s['device']
            if d['fault'] or d['status'] == 'RECOVERY_REQUIRED' or (d['paused'] and not force):
                return
            t = self.task(s, d['active_task']) if d['active_task'] else next((t for t in s['tasks'] if t['status']=='QUEUED'), None)
            if not t:
                d['status'] = 'IDLE'
                return
            if not d['active_task']:
                d['active_task'] = t['id']
                t['status'] = 'RUNNING'
                self.event(s, f'{t["number"]} 仿真设备接单', task_id=t['id'])
            d['status'] = 'RUNNING'
            dt = min(seconds, 1.0) * d['speed']
            phase = t['phase']
            if phase in ('MOVE_SOURCE', 'MOVE_TARGET'):
                pos = t['source'] if phase == 'MOVE_SOURCE' else t['target']
                x,y,_ = self.coords(s,pos)
                d['x'] = self.approach(d['x'],x,dt*1.8)
                d['y'] = self.approach(d['y'],y,dt*1.1)
                if abs(d['x']-x)<1e-6 and abs(d['y']-y)<1e-6:
                    t['phase'] = 'PICK' if phase=='MOVE_SOURCE' else 'PLACE'
                    t['phase_time'] = 0
            elif phase in ('PICK','PLACE'):
                key = t['source'] if phase=='PICK' else t['target']
                _,_,z = self.coords(s,key)
                t['phase_time'] += dt
                d['z'] = z * min(1.0,t['phase_time']/1.0)
                if t['phase_time'] >= 1.0:
                    p = self.place(s,key)
                    if phase=='PICK':
                        if p['load']!=t['load_id'] or d['carrying']:
                            raise DomainError('仿真取货条件不满足')
                        p['load']=None
                        d['carrying']=t['load_id']
                        s['loads'][t['load_id']]['location']='FORK'
                    else:
                        if p['load'] or d['carrying']!=t['load_id']:
                            raise DomainError('仿真放货条件不满足')
                        p['load']=t['load_id']
                        d['carrying']=None
                        s['loads'][t['load_id']]['location']=key
                    t['phase']='RETRACT_PICK' if phase=='PICK' else 'RETRACT_PLACE'
                    self.event(s, f'{t["number"]} {"取货" if phase=="PICK" else "放货"}完成：{key}', task_id=t['id'])
            elif phase.startswith('RETRACT'):
                d['z']=self.approach(d['z'],0,dt*1.4)
                if abs(d['z'])<1e-6:
                    if phase=='RETRACT_PICK':
                        t['phase']='MOVE_TARGET'
                    else:
                        self.complete(s,t)
        return self.mutate(apply)

    def complete(self,s,t):
        t.update(status='COMPLETED',phase='DONE',completed_at=now(),integration='PENDING')
        for key in (t['source'],t['target']):
            self.place(s,key)['reserved']=None
        s['device'].update(active_task=None,status='IDLE')
        s['outbox'].append(dict(event_id=str(uuid.uuid4()),task_id=t['id'],type='transport.completed',
                                load_id=t['load_id'],source=t['source'],target=t['target'],
                                created_at=now(),status='PENDING',mode='SIMULATION'))
        self.event(s,f'{t["number"]} 物理仿真完成；业务回执待确认。',task_id=t['id'])

    def control(self, action, value=None):
        def apply(s):
            d=s['device']
            if action=='pause': d['paused']=True
            elif action=='resume':
                if d['fault']: raise DomainError('请先清除仿真故障')
                d['paused']=False
                if d['active_task']:
                    self.task(s,d['active_task'])['status']='RUNNING'
                    d['status']='RUNNING'
                else: d['status']='IDLE'
            elif action=='speed':
                if value not in (1,2,5,10): raise DomainError('速度只支持 1、2、5、10 倍')
                d['speed']=value
            elif action=='fault':
                d.update(fault='SIM_AXIS_FAULT',status='FAULT',paused=True)
                if d['active_task']: self.task(s,d['active_task'])['status']='BLOCKED'
            elif action=='clear_fault':
                d.update(fault=None,status='RECOVERY_REQUIRED' if d['active_task'] else 'IDLE',paused=True)
                if d['active_task']: self.task(s,d['active_task'])['status']='RECOVERY_REQUIRED'
            elif action=='release_out':
                p=s['stations']['OUT']
                if p['reserved']: raise DomainError('出库站台仍被任务占用')
                if not p['load']: raise DomainError('出库站台没有载具')
                s['loads'][p['load']]['location']='DISPATCHED'
                p['load']=None
            else: raise DomainError('不支持的操作')
            self.event(s,f'仿真操作：{action}', 'warning' if action=='fault' else 'info')
        return self.mutate(apply)

    def cancel(self, task_id):
        def apply(s):
            t=self.task(s,task_id)
            if t['status']!='QUEUED': raise DomainError('仅允许取消尚未执行的排队任务')
            for key in (t['source'],t['target']): self.place(s,key)['reserved']=None
            if t['kind']=='INBOUND':
                s['stations']['IN']['load']=None
                s['loads'][t['load_id']]['location']='RETURNED'
            t['status']='CANCELLED'
            self.event(s,f'{t["number"]} 已取消；入库载具如有则退回站外。',task_id=t['id'])
        return self.mutate(apply)

    def acknowledge(self,event_id):
        def apply(s):
            event=next((e for e in s['outbox'] if e['event_id']==event_id),None)
            if not event: raise DomainError('回执不存在')
            if event['status']=='ACKNOWLEDGED_SIMULATION': return event
            event['status']='ACKNOWLEDGED_SIMULATION'
            self.task(s,event['task_id'])['integration']='ACKNOWLEDGED_SIMULATION'
            self.event(s,'模拟业务端已确认回执（未写入 Odoo）',task_id=event['task_id'])
            return event
        return self.mutate(apply)

    def validate(self,s):
        seen={}
        for key,p in {**s['locations'],**s['stations']}.items():
            if p['load']:
                if p['load'] in seen: raise DomainError('载具重复占位')
                seen[p['load']]=key
        if s['device']['carrying']:
            if s['device']['carrying'] in seen: raise DomainError('货叉载具重复')
            seen[s['device']['carrying']]='FORK'
        for ident,load in s['loads'].items():
            if load['location'] not in ('DISPATCHED','RETURNED') and seen.get(ident)!=load['location']:
                raise DomainError('载具位置与占用记录不一致')
