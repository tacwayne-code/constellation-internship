"""Manual original-program commands; durable intents, no automatic resend."""
import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str = Field(min_length=8, max_length=100)
    action: Literal['task', 'stop']
    task_id: int = Field(default=0, ge=0, le=32767)
    function: int = Field(default=4, ge=1, le=6)
    source_side: int = Field(default=1, ge=0, le=2)
    source_level: int = Field(default=1, ge=0, le=32767)
    source_column: int = Field(default=1, ge=0, le=32767)
    source_depth: int = Field(default=1, ge=0, le=2)
    target_side: int = Field(default=1, ge=0, le=2)
    target_level: int = Field(default=1, ge=0, le=32767)
    target_column: int = Field(default=1, ge=0, le=32767)
    target_depth: int = Field(default=1, ge=0, le=2)
    dock: int = Field(default=1, ge=0, le=2)
    site_ready: bool = False

    @model_validator(mode='after')
    def validate_zero_targets(self):
        if self.action == 'task':
            for prefix, zero_functions, label in [('source', (2, 4), '取箱目标'), ('target', (1, 4), '放箱目标')]:
                if self.function not in zero_functions:
                    fields = ('side', 'level', 'column', 'depth')
                    if any(getattr(self, f'{prefix}_{field}') == 0 for field in fields):
                        raise ValueError(f'{label}仅在{"入库" if prefix == "source" else "出库"}或移动时允许为0')
            if self.dock == 0 and self.function != 4:
                raise ValueError('码头号仅在移动时允许为0')
        return self


class ManualControl:
    def __init__(self, reader, path):
        self.reader = reader
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL, task_id INTEGER)')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_task ON commands(task_id) WHERE task_id IS NOT NULL')

    def db(self):
        return sqlite3.connect(self.path)

    def execute(self, command):
        payload = command.model_dump_json()
        r = self.reader
        with r.lock, self.db() as db:
            existing = db.execute('SELECT payload,result FROM commands WHERE id=?', (command.request_id,)).fetchone()
            if existing:
                if existing[0] != payload:
                    raise HTTPException(409, '同一请求编号的内容不能改变')
                return json.loads(existing[1])
            if not command.site_ready:
                raise HTTPException(409, '请确认现场控制权和动作区域已核对')
            if command.action == 'task':
                required = {'task_id', 'function', 'source_side', 'source_level', 'source_column', 'source_depth',
                            'target_side', 'target_level', 'target_column', 'target_depth', 'dock'}
                if not required.issubset(command.model_fields_set):
                    raise HTTPException(422, '任务参数必须全部明确填写，不能使用隐含默认库位')
            r.poll()
            if not r.snapshot()['live'] or not r.client:
                raise HTTPException(409, 'PLC 反馈不可用，未写入')
            raw = bytes(r.client.db_read(2, 0, 32))
            words = struct.unpack('>16h', raw)
            if command.action == 'task':
                if r.data['cpu_state'] != 'S7CpuStatusRun' or words[0] != 8 or words[2] != 0 or words[3] != 1:
                    raise HTTPException(409, f'未满足远程接单条件：状态={words[0]}，报警={words[2]}，数据交互状态={words[3]}（需1）；请在现场HMI核对远程模式及任务许可')
                if words[14] != 0:
                    raise HTTPException(409, 'PLC 命令码未清零，不覆盖待执行命令')
                if command.task_id == 0 or command.task_id in (words[1], words[15]):
                    raise HTTPException(409, '请使用非零且未使用的新任务号')
            result = dict(request_id=command.request_id, action=command.action,
                          time=datetime.now(timezone.utc).isoformat(), status='UNCERTAIN',
                          detail='写入结果未确定；禁止自动重发，需核对PLC', writes=[])
            try:
                db.execute('INSERT INTO commands VALUES (?,?,?,?)', (command.request_id, payload, json.dumps(result, ensure_ascii=False), command.task_id if command.action == 'task' else None))
                db.commit()  # Persist before first possible PLC write.
            except sqlite3.IntegrityError:
                raise HTTPException(409, '任务号已经使用，禁止重复执行')

            def write(offset, data):
                r.client.db_write(2, offset, bytearray(data))
                result['writes'].append(dict(db=2, offset=offset, hex=data.hex(' ')))
            try:
                if command.action == 'task':
                    params = [command.source_side, command.source_level, command.source_column, command.source_depth,
                              command.target_side, command.target_level, command.target_column, command.target_depth,
                              command.dock, command.function]
                    data = struct.pack('>10h', *params)
                    write(8, data)
                    write(30, struct.pack('>h', command.task_id))
                    if bytes(r.client.db_read(2, 8, 20)) != data or bytes(r.client.db_read(2, 30, 2)) != struct.pack('>h', command.task_id):
                        raise RuntimeError('参数读回不一致，未发送启动；可能存在其他写入方')
                    # Re-check feedback immediately before the final start command.
                    current = struct.unpack('>4h', bytes(r.client.db_read(2, 0, 8)))
                    if current[0] != 8 or current[2] != 0 or current[3] != 1:
                        raise RuntimeError('参数写入后接单条件改变，未发送启动')
                    write(28, struct.pack('>h', 1))
                else:
                    # Original program network "系统停止": remote command 2.
                    write(28, struct.pack('>h', 2))
                result.update(status='SENT', detail='S7写入已返回；请依据实时反馈确认实际执行，未宣称动作完成')
            except Exception as error:
                result.update(status='UNCERTAIN', detail=str(error) + '；不自动重发，请核对PLC')
                r.data.update(connected=False, last_error=str(error))
                r.close()
            db.execute('UPDATE commands SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), command.request_id))
            db.commit()
            return result

    def recent(self):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT result FROM commands ORDER BY rowid DESC LIMIT 30')]
