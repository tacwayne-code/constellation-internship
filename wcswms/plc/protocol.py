"""SIM_V1: explicit simulator contract; DB2 word names derive from supplied table.

DBW6 and DB100/101 are SIM ONLY. DB2 addresses and status codes follow the supplied table.
All S7 numeric values are big endian. Task identifiers are 1..32767, no reuse.
"""
import struct

PHASES = ['IDLE', 'MOVE_SOURCE', 'PICK', 'RETRACT_PICK', 'MOVE_TARGET', 'PLACE', 'RETRACT_PLACE', 'DONE']
ACTIONS = {'pause':1, 'resume':2, 'step':3, 'fault':4, 'clear_fault':5, 'speed':6, 'ack':7}
STATUS_LABELS = {1:'出库完成',2:'入库完成',3:'调仓完成',4:'移动完成',5:'取货完成',6:'放货完成',7:'异常',8:'空闲',9:'忙碌',10:'复位完成'}
LABELS = ['设备状态','完成任务号','报警码','已接单任务号（SIM握手）',
          '源排','源层','源列','源伸位','目标排','目标层','目标列','目标伸位',
          '站台号','功能号','命令字','任务号']


def u16(buf, offset): return struct.unpack_from('>H',buf,offset)[0]
def u32(buf, offset): return struct.unpack_from('>I',buf,offset)[0]
def f32(buf, offset): return struct.unpack_from('>f',buf,offset)[0]
def put16(buf, offset, value): struct.pack_into('>H',buf,offset,int(value))
def put32(buf, offset, value): struct.pack_into('>I',buf,offset,int(value))
def putreal(buf, offset, value): struct.pack_into('>f',buf,offset,float(value))


def location_words(key):
    if key in ('IN','OUT'): return [0,0,0,0]
    side,column,level=key.split('-')
    return [1 if side=='L' else 2,int(level),int(column),1]


def encode_task(task):
    values=location_words(task['source'])+location_words(task['target'])
    values += [1 if task['kind']=='INBOUND' else 2 if task['kind']=='OUTBOUND' else 0,
               task['plc_function'],1,task['plc_id']]
    return struct.pack('>12H',*values)


def decode_task(db):
    function=u16(db,26)
    def location(offset):
        row,level,column,depth=struct.unpack_from('>4H',db,offset)
        if row not in (1,2) or depth!=1: raise ValueError('SIM支持单深位、排号1/2')
        return f'{"L" if row==1 else "R"}-{column:02}-{level:02}'
    if function not in (1,2,3): raise ValueError('SIM仅支持入库、出库、移库')
    return dict(id=u16(db,30),function=function,source='IN' if function==2 else location(8),
                target='OUT' if function==1 else location(16))


def decode_telemetry(db):
    if u16(db,0)!=0x5753 or u16(db,2)!=1: raise ValueError('不是SIM_V1软PLC')
    phase=u16(db,28)
    p=dict(scan=u32(db,4),boot=u32(db,8),runtime_ms=u32(db,12),x=f32(db,16),y=f32(db,20),z=f32(db,24),
           phase=PHASES[phase] if phase<len(PHASES) else 'UNKNOWN',active_task=u16(db,30),
           carrying=bool(u16(db,32)),progress=u16(db,34),paused=bool(u16(db,36)),fault=u16(db,38),
           speed=u16(db,40),recovery=bool(u16(db,42)),control_ack=u32(db,44),
           lease_remaining_ms=u32(db,48),pid=u32(db,52),completed_total=u32(db,56),
           status=u16(db,64),completed_id=u16(db,66),accepted_id=u16(db,68))
    p['cpu_state']='FAULT' if p['fault'] else 'RECOVERY_REQUIRED' if p['recovery'] else 'PAUSED' if p['paused'] else 'RUN'
    p['scan_count']=p['scan']
    return p


def registers(raw):
    return [dict(db=2,offset=i*2,address=f'DB2.DBW{i*2}',label=label,value=u16(raw,i*2),type='INT',
                 description=STATUS_LABELS.get(u16(raw,0),'尚无有效反馈') if i==0 else '')
            for i,label in enumerate(LABELS)]
