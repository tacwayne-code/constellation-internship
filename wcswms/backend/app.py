import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

from .engine import Engine, DomainError
from .plc_controller import PlcController
from .native_plc import snapshot as native_snapshot

ROOT=Path(__file__).resolve().parents[1]


class TaskRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    request_id: str=Field(min_length=1,max_length=100)
    kind: Literal['INBOUND','OUTBOUND','TRANSFER']
    load_id: str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    target: str='AUTO'
    sku: str=Field(default='SKU-001',min_length=1,max_length=64)
    quantity: int=Field(default=1,ge=1,le=1000000)
    weight_kg: float=Field(default=10,gt=0,le=150,allow_inf_nan=False)


class Control(BaseModel):
    action: Literal['pause','resume','speed','fault','clear_fault','release_out','step','disconnect','reconnect']
    value: int|None=None


def create_app(db_path=None,automatic=True,plc_config=None):
    config=plc_config if plc_config is not None else json.loads(Path(os.environ.get('WCS_PLC_CONFIG',str(ROOT/'config/plc.json'))).read_text(encoding='utf-8-sig'))
    if config.get('profile') in ('PHYSICAL_READONLY', 'PHYSICAL_CONTROL') and automatic:
        from .physical_app import create_app as create_physical_app
        return create_physical_app(config)
    engine=Engine(db_path or os.environ.get('WCS_DB',str(ROOT/'data/warehouse.sqlite3')),
                  json.loads((ROOT/'config/layout.json').read_text(encoding='utf-8-sig')),recover=False)
    use_plc=config.get('enabled',True) and (automatic or plc_config is not None) and os.environ.get('WCS_PLC_ENABLED','1')!='0'
    controller=PlcController(engine,config) if use_plc else None

    @asynccontextmanager
    async def lifespan(app):
        engine.mutate(engine.recover)
        async def runner():
            while True:
                await asyncio.sleep(.1)
                try: await asyncio.to_thread(controller.tick if controller else engine.tick,.1)
                except Exception:
                    logging.exception('Simulation halted')
                    if controller: controller.fail('WCS控制循环异常，请检查日志')
                    else: engine.control('fault')
        worker=asyncio.create_task(runner()) if automatic else None
        yield
        if worker:
            worker.cancel()
            try: await worker
            except asyncio.CancelledError: pass
        if controller: controller.close()

    app=FastAPI(title='立库 WCS 仿真 MVP',version='0.2.0',lifespan=lifespan)
    app.state.engine=engine
    app.state.plc=controller

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'拒绝跨来源写入'},status_code=403)
        return await call_next(request)

    @app.exception_handler(DomainError)
    async def domain_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=409)

    @app.get('/api/state')
    def state():
        result=engine.read()
        if controller:
            result['mode']='SOFT_PLC_S7'
            result['plc']=controller.summary()
        return result

    @app.get('/api/health')
    def health(): return {'status':'ok','mode':'SOFT_PLC_S7' if controller else 'SIMULATION','real_plc':False,'real_odoo':False,'plc':controller.summary() if controller else None}

    @app.get('/api/native-plc')
    def native_plc(): return native_snapshot()

    @app.post('/api/tasks',status_code=201)
    def create(body:TaskRequest): return engine.create(body.model_dump())

    @app.post('/api/tasks/{task_id}/cancel')
    def cancel(task_id:str): engine.cancel(task_id); return {'ok':True}

    @app.post('/api/simulation/control')
    def control(body:Control):
        if controller:
            controller.control(body.action,body.value)
        elif body.action=='step':
            if not engine.read()['device']['paused']: raise DomainError('请先暂停再单步')
            engine.tick(.5,force=True)
        else: engine.control(body.action,body.value)
        return engine.read()['device']

    @app.get('/api/plc/diagnostics')
    def plc_diagnostics():
        return controller.diagnostics() if controller else {'mode':'SIMULATION','connected':False,'trace':[],'registers':[],'source_files':[]}

    @app.get('/api/plc/source')
    def plc_source(file:str): return PlcController.source(file)

    @app.post('/api/plc/control')
    def plc_control(body:Control):
        if not controller: raise DomainError('当前为独立Engine测试模式，PLC适配器未启用')
        controller.control(body.action,body.value)
        return controller.diagnostics()

    @app.get('/api/integration/outbox')
    def outbox(): return {'mode':'SIMULATION','odoo_connected':False,'events':engine.read()['outbox']}

    @app.post('/api/integration/outbox/{event_id}/ack-simulation')
    def ack(event_id:str): return engine.acknowledge(event_id)

    @app.get('/api/structure')
    def structure():
        p=ROOT/'assets/structure/manifest.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'files':[]}

    assets=ROOT/'assets'
    assets.mkdir(exist_ok=True)
    app.mount('/assets',StaticFiles(directory=assets),name='assets')
    dist=ROOT/'web/dist'
    if dist.exists(): app.mount('/',StaticFiles(directory=dist,html=True),name='web')
    return app


app=create_app()
