"""WCS adapter: every device step comes from S7 feedback, never Engine.tick()."""
import copy
import inspect
import hashlib
import ipaddress
import json
import os
import struct
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from snap7.client import Client
from snap7.type import Parameter
from plc.protocol import ACTIONS, decode_telemetry, encode_task, registers, u16
from .engine import DomainError, now

ROOT=Path(__file__).resolve().parents[1]
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCES={'plc/runtime.py','plc/protocol.py','backend/plc_controller.py'}


class PlcController:
    def __init__(self,engine,config):
        self.engine=engine
        self.config=config
        self.host=config['host']
        if not ipaddress.ip_address(self.host).is_loopback: raise ValueError('当前PLC适配器仅允许本机回环地址')
        self.port=int(os.environ.get('WCS_PLC_PORT',config['port']))
        self.trace_path=Path(os.environ.get('WCS_PLC_TRACE',str(ROOT/config['trace_file'])))
        self.wcs_trace_path=self.trace_path.with_name('wcs-trace.jsonl')
        self.wcs_trace_path.parent.mkdir(parents=True,exist_ok=True)
        self.trace_log_error=None
        self.lock=threading.RLock()
        self.client=None
        self.connected=False
        self.enabled=True
        self.tx_count=self.rx_count=0
        self.last_error=None
        self.last_feedback=None
        self.telemetry={}
        self.db2=bytes(32)
        self.history=deque(maxlen=180)
        self.heartbeat=0
        self.sequence=0
        self.recovery_hold=bool(engine.read()['device']['active_task'])
        self.last_retry=0.
        self.last_tx=None
        self.last_rx=None

    def trace(self,event,message,**extra):
        frame=inspect.currentframe().f_back
        stamp=now()
        row=dict(id='WCS-'+uuid.uuid4().hex,time=stamp,timestamp=stamp,process='WCS',event=event,
                 file='backend/plc_controller.py',source_sha256=SOURCE_SHA256,function=frame.f_code.co_name,line=frame.f_lineno,
                 scan=self.telemetry.get('scan'),message=message,**extra)
        self.history.append(row)
        important=event in {'DISPATCH','INVENTORY_PICK','INVENTORY_PLACE','COMMIT_COMPLETE','CONNECT','DISCONNECT'} or (event=='S7_WRITE' and (extra.get('db')==2 or (extra.get('db')==101 and extra.get('offset')==0)))
        if important:
            try:
                if self.wcs_trace_path.exists() and self.wcs_trace_path.stat().st_size>2_000_000:
                    retained=self.wcs_trace_path.read_text(encoding='utf-8').splitlines()[-1000:]
                    self.wcs_trace_path.write_text('\n'.join(retained)+'\n',encoding='utf-8')
                with self.wcs_trace_path.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                self.trace_log_error=None
            except OSError as error:
                # Diagnostic storage is separate from inventory/physical persistence. Surface any gap.
                self.trace_log_error=str(error)
        return row

    def close(self):
        if self.client:
            try: self.client.disconnect()
            except Exception: pass
            self.client=None
        self.connected=False

    def connect(self):
        self.client=Client(auto_reconnect=False)
        self.client.set_param(Parameter.RecvTimeout,800)
        self.client.set_param(Parameter.SendTimeout,800)
        self.client.connect(self.host,0,1,tcp_port=self.port)
        self.connected=True
        self.last_error=None
        if self.engine.read()['device']['active_task']: self.recovery_hold=True
        self.trace('CONNECT',f'S7 TCP连接已建立 {self.host}:{self.port}')

    def write(self,db,offset,payload,decoded):
        self.client.db_write(db,offset,bytearray(payload))
        self.tx_count+=1
        self.last_tx=now()
        self.trace('S7_WRITE',f'实际S7写入 DB{db}，偏移{offset}，{len(payload)}字节',direction='TX',db=db,offset=offset,hex=payload.hex(' '),decoded=decoded)

    def read_db(self,db,size):
        raw=bytes(self.client.db_read(db,0,size))
        self.rx_count+=1
        self.last_rx=now()
        decoded=decode_telemetry(raw) if db==100 else dict(words=[u16(raw,i) for i in range(0,size,2)])
        self.trace('S7_READ',f'实际S7读取 DB{db}，{size}字节',direction='RX',db=db,offset=0,hex=raw.hex(' '),decoded=decoded)
        return raw

    def heartbeat_write(self):
        self.heartbeat=(self.heartbeat+1)&0xffffffff or 1
        self.write(101,8,struct.pack('>I',self.heartbeat),dict(heartbeat=self.heartbeat))

    def send_control(self,action,value=0):
        self.sequence=max(self.sequence,self.telemetry.get('control_ack',0))+1
        self.write(101,0,struct.pack('>IHH',self.sequence,ACTIONS[action],value or 0),dict(sequence=self.sequence,action=action,value=value))
        return self.sequence

    def fail(self,error):
        if self.connected: self.trace('DISCONNECT',str(error))
        self.last_error=str(error)
        self.close()
        self.recovery_hold=True
        def freeze(s):
            d=s['device']
            d.update(status='DISCONNECTED',paused=True)
            if d['active_task']: self.engine.task(s,d['active_task'])['status']='RECOVERY_REQUIRED'
        self.engine.mutate(freeze)

    def tick(self,seconds=.1,force=False):
        with self.lock:
            if not self.enabled: return
            try:
                if not self.connected:
                    if time.monotonic()-self.last_retry<1: return
                    self.last_retry=time.monotonic()
                    self.connect()
                self.heartbeat_write()
                raw=self.read_db(100,80)
                self.telemetry=decode_telemetry(raw)
                self.db2=self.read_db(2,32)
                self.last_feedback=now()
                p=self.telemetry
                active=self.engine.read()['device']['active_task']
                self.apply_feedback(p)
                if self.recovery_hold and p['active_task'] and not p['paused']:
                    self.send_control('pause')
                state=self.engine.read()
                if p['phase']=='DONE':
                    done=next((t for t in state['tasks'] if t.get('plc_id')==p['completed_id'] and t['status']=='COMPLETED'),None)
                    if done: self.send_control('ack',p['completed_id'])
                elif p['active_task']==0 and p['phase']=='IDLE' and not p['paused'] and not p['fault'] and not p['recovery']:
                    if active:
                        task=self.engine.task(state,active)
                        if task.get('plc_id') and p['accepted_id']<task['plc_id'] and not task.get('physical_progress',0):
                            self.write(2,8,encode_task(task),dict(task_id=task['id'],plc_id=task['plc_id'],source=task['source'],target=task['target']))
                    elif not self.recovery_hold:
                        task=self.claim_task()
                        if task: self.write(2,8,encode_task(task),dict(task_id=task['id'],plc_id=task['plc_id'],source=task['source'],target=task['target']))
            except Exception as error:
                self.fail(error)

    def claim_task(self):
        def claim(s):
            if s['device']['active_task']: return None
            task=next((t for t in s['tasks'] if t['status']=='QUEUED'),None)
            if not task: return None
            number=max(s.get('plc_sequence',0),self.telemetry.get('accepted_id',0))+1
            if number>32767: raise DomainError('SIM任务号已耗尽，请归档并成对重置PLC和WCS仿真状态')
            s['plc_sequence']=number
            task.update(plc_id=number,status='RUNNING',physical_progress=0)
            s['device'].update(active_task=task['id'],status='RUNNING')
            self.engine.event(s,f'{task["number"]} 交给独立软PLC，S7任务号 {number}',task_id=task['id'])
            return task
        task=self.engine.mutate(claim)
        if task: self.trace('DISPATCH',f'WCS预留成功，准备S7下发任务 {task["plc_id"]}')
        return task

    def apply_feedback(self,p):
        committed_events=[]
        def apply(s):
            d=s['device']
            task=self.engine.task(s,d['active_task']) if d['active_task'] else None
            if p['active_task'] and (not task or task.get('plc_id')!=p['active_task']):
                done=next((t for t in s['tasks'] if t.get('plc_id')==p['active_task'] and t['status']=='COMPLETED'),None)
                if not done:
                    self.recovery_hold=True
                    d.update(status='RECOVERY_REQUIRED',paused=True,fault='PLC_TASK_MISMATCH')
                    return
            d.update(x=p['x'],y=p['y'],z=p['z'],speed=p['speed'],paused=p['paused'] or self.recovery_hold,
                     fault=f'PLC_ALARM_{p["fault"]}' if p['fault'] else None)
            d['status']='FAULT' if p['fault'] else 'RECOVERY_REQUIRED' if p['recovery'] or self.recovery_hold else 'RUNNING' if task else 'IDLE'
            if not task: return
            if p['active_task']!=task.get('plc_id'):
                if p['active_task']==0 and p['accepted_id']<task.get('plc_id',0) and not task.get('physical_progress',0): return  # safe same-ID retry after ambiguous write
                task['status']='RECOVERY_REQUIRED'
                d.update(status='RECOVERY_REQUIRED',paused=True)
                self.recovery_hold=True
                return
            task['phase']=p['phase']
            task['status']='BLOCKED' if p['fault'] else 'RECOVERY_REQUIRED' if p['recovery'] or self.recovery_hold else 'RUNNING'
            progress=p['progress']
            previous=task.get('physical_progress',0)
            if progress<previous: raise DomainError('PLC物理进度回退，停止库存提交')
            if progress>=1 and previous<1:
                source=self.engine.place(s,task['source'])
                if source['load']!=task['load_id']: raise DomainError('PLC取货反馈与WMS源载具不符')
                source['load']=None
                d['carrying']=task['load_id']
                s['loads'][task['load_id']]['location']='FORK'
                self.engine.event(s,f'{task["number"]} PLC传感器反馈取货完成',task_id=task['id'])
                committed_events.append(('INVENTORY_PICK','收到PLC progress≥1，载具至货叉事务已提交'))
            if progress>=2 and previous<2:
                destination=self.engine.place(s,task['target'])
                if destination['load'] or d['carrying']!=task['load_id']: raise DomainError('PLC放货反馈与WMS目标/货叉不符')
                destination['load']=task['load_id']
                d['carrying']=None
                s['loads'][task['load_id']]['location']=task['target']
                self.engine.event(s,f'{task["number"]} PLC传感器反馈放货完成',task_id=task['id'])
                committed_events.append(('INVENTORY_PLACE','收到PLC progress=2，目标库位占用事务已提交'))
            task['physical_progress']=progress
            if p['phase']=='DONE' and p['completed_id']==task['plc_id'] and progress==2 and abs(p['z'])<1e-5:
                self.engine.complete(s,task)
                s['outbox'][-1]['mode']='SOFT_PLC_S7'
                committed_events.append(('COMMIT_COMPLETE',f'PLC完成号 {p["completed_id"]} 已核对；WMS事务提交完成'))
        self.engine.mutate(apply)
        for event,message in committed_events:
            self.trace(event,message)  # Record commits only after SQLite validation and commit succeed.

    def control(self,action,value=None):
        with self.lock:
            if action=='disconnect':
                self.enabled=False
                self.fail('用户注入S7通信断连；PLC将由心跳看门狗冻结')
                return
            if action=='reconnect':
                self.enabled=True
                self.last_retry=0
                self.tick()
                return
            if action=='release_out': return self.engine.control(action,value)
            if action not in ACTIONS or action=='ack': raise DomainError('不支持的PLC控制')
            if not self.connected: raise DomainError('PLC未连接；未下发控制')
            if action=='speed' and value not in (1,2,5,10): raise DomainError('速度只支持1、2、5、10倍')
            if action=='resume':
                state=self.engine.read()
                active=state['device']['active_task']
                if active and not self.telemetry.get('active_task') and self.engine.task(state,active).get('physical_progress',0):
                    raise DomainError('PLC已丢失活动任务，但WCS已有物理载荷进度，禁止自动重放')
                if self.telemetry.get('active_task'):
                    if not active or self.engine.task(state,active).get('plc_id')!=self.telemetry['active_task']:
                        raise DomainError('WCS与PLC任务号不一致，禁止恢复')
                if self.telemetry.get('fault'): raise DomainError('请先清除PLC故障')
            try:
                self.heartbeat_write()
                seq=self.send_control(action,value or 0)
                deadline=time.monotonic()+1.2
                while time.monotonic()<deadline:
                    time.sleep(.025)
                    raw=self.read_db(100,80)
                    self.telemetry=decode_telemetry(raw)
                    if self.telemetry['control_ack']==seq:
                        if u16(raw,70): raise DomainError(f'PLC拒绝控制 {action}，SIM错误码 {u16(raw,70)}')
                        if action=='resume': self.recovery_hold=False
                        self.apply_feedback(self.telemetry)
                        return
                raise RuntimeError('PLC控制确认超时')
            except DomainError: raise
            except Exception as error:
                self.fail(error)
                raise DomainError(f'S7控制失败：{error}') from error

    def summary(self):
        return dict(mode='SOFT_PLC_S7',profile='SIM_V1',connected=self.connected,endpoint=f'{self.host}:{self.port}',
                    cpu_state=self.telemetry.get('cpu_state','OFFLINE') if self.connected else 'OFFLINE',
                    scan=self.telemetry.get('scan',0),phase=self.telemetry.get('phase','UNKNOWN'),last_error=self.last_error,
                    real_plc=False,original_program_executed=False,last_feedback=self.last_feedback,recovery_hold=self.recovery_hold)

    def diagnostics(self):
        with self.lock:
            rows=list(self.history)[-100:]
            for path,count in ((self.trace_path,70),(self.wcs_trace_path,70)):
                if path.exists():
                    try:
                        with path.open('rb') as stream:
                            stream.seek(max(0,path.stat().st_size-180000))
                            for line in stream.read().decode('utf-8',errors='replace').splitlines()[-count:]:
                                try: rows.append(json.loads(line))
                                except ValueError: pass
                    except OSError as error: self.trace_log_error=str(error)
            rows=list({row['id']:row for row in rows}.values())
            rows.sort(key=lambda row:row['time'])
            return dict(**self.summary(),host=self.host,port=self.port,transport='S7 TCP / ISO-on-TCP',
                        plc=copy.deepcopy(self.telemetry),cycle_ms=self.config['scan_interval_ms'],
                        tx_count=self.tx_count,rx_count=self.rx_count,last_tx=self.last_tx,last_rx=self.last_rx,
                        trace=rows,registers=registers(self.db2),trace_log_error=self.trace_log_error,
                        trace_files=dict(plc=str(self.trace_path),wcs=str(self.wcs_trace_path)),
                        source_files=[dict(id=file,label=file) for file in sorted(SOURCES)])

    @staticmethod
    def source(file):
        if file not in SOURCES: raise DomainError('源码路径不在允许清单')
        raw=(ROOT/file).read_bytes()
        content=raw.decode('utf-8-sig')
        return dict(file=file,path=file,language='python',content=content,sha256=hashlib.sha256(raw).hexdigest(),
                    lines=[dict(number=i,text=line) for i,line in enumerate(content.splitlines(),1)])
