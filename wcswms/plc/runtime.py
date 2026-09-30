"""Independent cyclic PLC simulator with a real S7 TCP server on loopback.

This Python scan program is intentionally NOT Siemens bytecode or a PLCSIM CPU.
DB2 follows the supplied word layout. Handshakes and auxiliary DBs are SIM_V1.
"""
import argparse
import inspect
import hashlib
import ipaddress
import json
import os
import signal
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from snap7.server import Server
from snap7.type import SrvArea
from .protocol import PHASES, decode_task, put16, put32, putreal, u16, u32

ROOT=Path(__file__).resolve().parents[1]
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class SoftPLC:
    def __init__(self,state_path,trace_path,layout,cycle=.05,lease=2.5):
        self.path=Path(state_path)
        self.trace_path=Path(trace_path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.trace_path.parent.mkdir(parents=True,exist_ok=True)
        self.layout=layout
        self.cycle=cycle
        self.lease=lease
        self.server=Server(log=False)
        self.db2=bytearray(32)
        self.db100=bytearray(80)
        self.db101=bytearray(16)
        for number,data in [(2,self.db2),(100,self.db100),(101,self.db101)]:
            self.server.register_area(SrvArea.DB,number,data)
        self.s=json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else dict(
            boot=0,scan=0,x=0.,y=.4,z=0.,task=None,phase='IDLE',phase_time=0.,progress=0,
            paused=False,fault=0,speed=1,recovery=False,accepted_id=0,completed_id=0,completed_total=0)
        self.s['boot']+=1
        self.s['control_ack']=0
        self.s['control_error']=0
        self.s['heartbeat']=0
        if self.s['task'] and self.s['phase']!='DONE':
            self.s.update(paused=True,recovery=True)
        self.started=time.monotonic()
        self.last_heartbeat=time.monotonic()
        self.step_requested=False
        self.trace('BOOT','软PLC启动；Python扫描程序，原厂PLC工程未执行')
        self.persist()
        self.publish()

    def trace(self,event,message,**extra):
        frame=inspect.currentframe().f_back
        stamp=datetime.now(timezone.utc).isoformat()
        record=dict(id='PLC-'+uuid.uuid4().hex,time=stamp,timestamp=stamp,process='PLC',event=event,
                    file='plc/runtime.py',source_sha256=SOURCE_SHA256,function=frame.f_code.co_name,line=frame.f_lineno,
                    scan=self.s['scan'],message=message,**extra)
        if self.trace_path.exists() and self.trace_path.stat().st_size>2_000_000:
            # Keep a bounded evidence log; no invented or replayed execution lines.
            lines=self.trace_path.read_text(encoding='utf-8').splitlines()[-1500:]
            self.trace_path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        with self.trace_path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')

    def persist(self):
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.s,ensure_ascii=False),encoding='utf-8')
        # Windows AV/readers may briefly hold the destination without delete sharing.
        # Bounded retry preserves the commit-before-feedback rule; a permanent error stops the CPU.
        for attempt,delay in enumerate((0,.01,.02,.05,.1)):
            if delay: time.sleep(delay)
            try:
                os.replace(temporary,self.path)
                return
            except PermissionError:
                if attempt==4: raise

    def coords(self,key):
        if key=='IN': return 0.,.4,-self.layout['aisle_half_width_m']
        if key=='OUT': return 0.,.4,self.layout['aisle_half_width_m']
        side,column,level=key.split('-')
        column,level=int(column),int(level)
        if not 1<=column<=self.layout['columns'] or not 1<=level<=self.layout['levels']:
            raise ValueError('库位超过SIM配置')
        return column*self.layout['column_pitch_m'],.4+(level-1)*self.layout['level_pitch_m'],(-1 if side=='L' else 1)*self.layout['aisle_half_width_m']

    def read_inputs(self):
        self.server.lock_area(SrvArea.DB,2)
        try: command=bytes(self.db2)
        finally: self.server.unlock_area(SrvArea.DB,2)
        self.server.lock_area(SrvArea.DB,101)
        try: controls=bytes(self.db101)
        finally: self.server.unlock_area(SrvArea.DB,101)
        heartbeat=u32(controls,8)
        if heartbeat!=self.s['heartbeat']:
            self.s['heartbeat']=heartbeat
            self.last_heartbeat=time.monotonic()
        seq,action,value=u32(controls,0),u16(controls,4),u16(controls,6)
        if seq and seq!=self.s['control_ack']:
            self.apply_control(action,value)
            self.s['control_ack']=seq
            self.trace('CONTROL',f'S7 DB101控制 {action}，结果 {self.s["control_error"]}',direction='RX',db=101,offset=0,hex=controls[:8].hex(' '),decoded=dict(sequence=seq,action=action,value=value))
        if self.s['task'] and self.s['phase']!='DONE' and time.monotonic()-self.last_heartbeat>self.lease:
            if not self.s['recovery']:
                self.s.update(paused=True,recovery=True,fault=901)
                self.trace('WATCHDOG','WCS心跳超时；设备冻结，需清故障并核对恢复')
        command_word=u16(command,28)
        if command_word!=self.s.get('last_command_word',0):
            self.s['last_command_word']=command_word
            if command_word==2: self.s['paused']=True
            elif command_word==3: self.s.update(fault=102,paused=True)
        ident=u16(command,30)
        if u16(command,28)==1 and ident and ident!=self.s['accepted_id'] and self.s['task'] is None:
            if ident<=self.s['accepted_id']:
                self.s.update(fault=902,paused=True)
                return
            try:
                task=decode_task(command)
                if ident>32767: raise ValueError('任务号超出SIM INT范围')
                self.coords(task['source']); self.coords(task['target'])
                self.s.update(task=task,phase='MOVE_SOURCE',phase_time=0.,progress=0,accepted_id=ident,completed_id=0)
                self.trace('ACCEPT',f'接单 {ident}: {task["source"]} → {task["target"]}',direction='RX',db=2,offset=8,hex=command[8:].hex(' '),decoded=task)
            except ValueError as error:
                self.s.update(fault=903,paused=True)
                self.trace('REJECT',str(error))

    def apply_control(self,action,value):
        s=self.s
        s['control_error']=0
        if action==1: s['paused']=True
        elif action==2:
            if s['fault'] or time.monotonic()-self.last_heartbeat>self.lease:
                s['control_error']=1
            else: s.update(paused=False,recovery=False)
        elif action==3:
            if not s['paused'] or s['fault'] or s['recovery']: s['control_error']=2
            else: self.step_requested=True
        elif action==4: s.update(fault=101,paused=True)
        elif action==5: s.update(fault=0,paused=True,recovery=bool(s['task'] and s['phase']!='DONE'))
        elif action==6:
            if value not in (1,2,5,10): s['control_error']=3
            else: s['speed']=value
        elif action==7:
            if s['phase']=='DONE' and s['completed_id']==value:
                s.update(task=None,phase='IDLE',progress=0)
                self.trace('COMPLETE_ACK',f'WCS已提交任务 {value} 库存，PLC释放完成握手')
            elif s['task'] is not None: s['control_error']=4
        else: s['control_error']=5

    @staticmethod
    def approach(value,goal,amount): return value+max(-amount,min(amount,goal-value))

    def advance_motion(self,dt):
        s=self.s
        if not s['task'] or s['phase']=='DONE' or s['fault'] or s['recovery']: return
        if s['paused'] and not self.step_requested: return
        self.step_requested=False
        dt*=s['speed']
        phase=s['phase']
        task=s['task']
        if phase in ('MOVE_SOURCE','MOVE_TARGET'):
            x,y,_=self.coords(task['source'] if phase=='MOVE_SOURCE' else task['target'])
            s['x']=self.approach(s['x'],x,dt*1.8)
            s['y']=self.approach(s['y'],y,dt*1.1)
            if abs(s['x']-x)<1e-6 and abs(s['y']-y)<1e-6:
                s['phase']='PICK' if phase=='MOVE_SOURCE' else 'PLACE'
                s['phase_time']=0.
        elif phase in ('PICK','PLACE'):
            _,_,z=self.coords(task['source'] if phase=='PICK' else task['target'])
            s['phase_time']+=dt
            s['z']=z*min(1.,s['phase_time'])
            if s['phase_time']>=1.:
                s['progress']=1 if phase=='PICK' else 2
                s['phase']='RETRACT_PICK' if phase=='PICK' else 'RETRACT_PLACE'
                self.trace('SENSOR',f'模拟载荷传感器确认 {"取货" if phase=="PICK" else "放货"}，progress={s["progress"]}')
        elif phase in ('RETRACT_PICK','RETRACT_PLACE'):
            s['z']=self.approach(s['z'],0.,dt*1.4)
            if abs(s['z'])<1e-6:
                if phase=='RETRACT_PICK': s['phase']='MOVE_TARGET'
                else:
                    s.update(phase='DONE',completed_id=task['id'])
                    s['completed_total']+=1
                    self.trace('COMPLETE',f'任务 {task["id"]} 运动完成，保持握手等待WCS确认')
        if s['phase']!=phase:
            self.trace('PHASE',f'{phase} → {s["phase"]}')

    def publish(self):
        s=self.s
        # DBW0 follows the original interaction table: idle8/busy9/abnormal7/completion=function.
        status=7 if s['fault'] else s['task']['function'] if s['phase']=='DONE' else 9 if s['task'] else 8
        alarm=2 if s['fault']==101 else 1 if s['fault']==102 else 0
        self.server.lock_area(SrvArea.DB,2)
        try:
            for offset,value in [(0,status),(2,s['completed_id']),(4,alarm),(6,s['accepted_id'])]: put16(self.db2,offset,value)
        finally: self.server.unlock_area(SrvArea.DB,2)
        tele=bytearray(80)
        for offset,value in [(0,0x5753),(2,1),(28,PHASES.index(s['phase'])),(30,s['task']['id'] if s['task'] else 0),
                             (32,s['progress']==1),(34,s['progress']),(36,s['paused']),(38,s['fault']),(40,s['speed']),
                             (42,s['recovery']),(64,status),(66,s['completed_id']),(68,s['accepted_id']),(70,s['control_error'])]:
            put16(tele,offset,value)
        for offset,value in [(4,s['scan']),(8,s['boot']),(12,int((time.monotonic()-self.started)*1000)),
                             (44,s['control_ack']),(48,max(0,int((self.lease-time.monotonic()+self.last_heartbeat)*1000))),
                             (52,os.getpid()),(56,s['completed_total'])]: put32(tele,offset,value&0xffffffff)
        for offset,key in [(16,'x'),(20,'y'),(24,'z')]: putreal(tele,offset,s[key])
        self.server.lock_area(SrvArea.DB,100)
        try: self.db100[:]=tele
        finally: self.server.unlock_area(SrvArea.DB,100)

    def scan_once(self):
        self.s['scan']+=1
        self.read_inputs()
        self.advance_motion(self.cycle)
        self.persist()  # Persist physical effects before exposing feedback to WCS.
        self.publish()
        if self.s['scan']%10==0:
            self.trace('SCAN',f'循环扫描；{self.s["phase"]}；x={self.s["x"]:.3f}, y={self.s["y"]:.3f}, z={self.s["z"]:.3f}')

    def run(self,host,port,stop):
        if not ipaddress.ip_address(host).is_loopback: raise ValueError('软PLC只允许绑定loopback')
        self.server.start_to(host,port)
        try:
            while not stop.is_set():
                start=time.monotonic()
                self.scan_once()
                stop.wait(max(.001,self.cycle-(time.monotonic()-start)))
        except Exception as error:
            self.s.update(paused=True,recovery=True)
            self.trace('FATAL',f'扫描程序停止；未持久化状态不会发布：{error}')
            raise
        finally:
            try: self.persist()
            finally:
                self.server.stop()
                self.server.destroy()


def main():
    parser=argparse.ArgumentParser(description='SIM_V1本机S7协议软PLC，不执行Siemens程序')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=1102)
    parser.add_argument('--state',default=str(ROOT/'data/plc-state.json'))
    parser.add_argument('--trace',default=str(ROOT/'data/plc-trace.jsonl'))
    parser.add_argument('--layout',default=str(ROOT/'config/layout.json'))
    parser.add_argument('--cycle-ms',type=int,default=50)
    parser.add_argument('--lease-ms',type=int,default=2500)
    args=parser.parse_args()
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM,*([signal.SIGBREAK] if hasattr(signal,'SIGBREAK') else [])): signal.signal(sig,lambda *_:stop.set())
    plc=SoftPLC(args.state,args.trace,json.loads(Path(args.layout).read_text(encoding='utf-8-sig')),args.cycle_ms/1000,args.lease_ms/1000)
    plc.run(args.host,args.port,stop)


if __name__=='__main__': main()
