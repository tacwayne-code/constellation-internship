"""Warehouses and independent device workspaces; no shared PLC ownership."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3
import threading

from .wms_device import plc_ipv4, save_config
from .wms_service import WmsReader, feedback
from .physical_control import ManualControl
from .wms_store import WmsError, now


class Sites:
    def __init__(self, app, folder, config, config_path, commands_path, factory):
        self.folder, self.factory = Path(folder), factory
        self.path = self.folder / 'sites.sqlite3'
        self.lock = threading.RLock()
        self.apps = {'primary': app}
        self.loop, self.workers, self.automatic, self.closing = None, {}, False, False
        self.commands = {'primary': Path(commands_path)}
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS warehouses (
                id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                note TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, warehouse_id TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                revision INTEGER NOT NULL, UNIQUE(warehouse_id,name));''')
            db.execute("INSERT OR IGNORE INTO warehouses VALUES('main','默认仓库','原有库存与设备',1)")
            db.execute("INSERT OR IGNORE INTO devices VALUES('primary','main','主设备',1)")
            rows = list(db.execute("SELECT * FROM devices WHERE id!='primary'"))
        app.state.config = config
        app.state.scope_id = 'primary'
        app.state.sites = self
        for row in rows:
            self._load(row['id'])

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def name(value):
        value = value.strip()
        if not 1 <= len(value) <= 40:
            raise WmsError('名称须为 1—40 个字符')
        return value

    def _load(self, ident):
        # IDs come only from our registry; never accept a user-supplied file path.
        if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise RuntimeError('Invalid saved device ID')
        folder = self.folder / 'devices' / ident
        path = folder / 'wms.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        host = plc_ipv4(config['host'])
        port = config.get('port',102)
        if type(port) is not int or not 1 <= port <= 65535:
            raise WmsError('设备配置中的端口无效')
        if any(plc_ipv4(other.state.config['host'])==host and other.state.config.get('port',102)==port
               for other in self.apps.values()):
            raise WmsError('保存的设备配置重复绑定同一 PLC，请修正后再启动')
        commands = folder / 'commands.sqlite3'
        child = self.factory(config, path, folder, commands)
        child.state.scope_id, child.state.sites, child.state.config = ident, self, config
        self.apps[ident], self.commands[ident] = child, commands
        self.apps['primary'].mount('/api/scopes/' + ident, child)
        if self.loop and self.automatic:
            self.loop.call_soon_threadsafe(self._start_worker, ident)
        return child

    def scope(self, ident):
        with self.db() as db:
            row = db.execute('SELECT devices.id AS device_id,devices.name AS device_name,warehouses.id AS warehouse_id,warehouses.name AS warehouse_name '
                             'FROM devices JOIN warehouses ON warehouses.id=devices.warehouse_id WHERE devices.id=?', (ident,)).fetchone()
            if not row:
                raise WmsError('设备不存在，请重新选择')
            return dict(row)

    def snapshot(self):
        with self.db() as db:
            warehouses = [dict(r) for r in db.execute('SELECT * FROM warehouses ORDER BY rowid')]
            devices = [dict(r) for r in db.execute('SELECT * FROM devices ORDER BY rowid')]
            for row in devices:
                app = self.apps[row['id']]
                config = app.state.config
                snap = app.state.service.reader.snapshot()
                row.update(host=config['host'], port=config.get('port',102), rack=config.get('rack',0), slot=config.get('slot',1),
                           enabled=app.state.service.enabled, live=snap.get('live',False),
                           inbound_dock=config['inbound_dock'], outbound_dock=config['outbound_dock'])
            return dict(warehouses=warehouses, devices=devices, default_device='primary')

    def warehouse(self, name, note, ident=None, revision=None):
        name, note = self.name(name), note.strip()
        with self.db() as db:
            if ident:
                row = db.execute('SELECT * FROM warehouses WHERE id=?',(ident,)).fetchone()
                if not row or row['revision'] != revision:
                    raise WmsError('仓库资料已改变，请刷新后重试')
            try:
                if ident:
                    db.execute('UPDATE warehouses SET name=?,note=?,revision=revision+1 WHERE id=?',(name,note,ident))
                else:
                    ident=secrets.token_hex(16)
                    db.execute('INSERT INTO warehouses VALUES(?,?,?,1)',(ident,name,note))
            except sqlite3.IntegrityError:
                raise WmsError('仓库名称已存在') from None
            return dict(db.execute('SELECT * FROM warehouses WHERE id=?',(ident,)).fetchone())

    def _unique_endpoint(self, db, host, port, except_id=None):
        for row in db.execute('SELECT id FROM devices'):
            if row['id'] != except_id:
                config = self.apps[row['id']].state.config
                if config['host'] == host and config.get('port',102) == port:
                    raise WmsError('该 PLC 地址和端口已绑定其他设备，不能同时操作同一台 PLC')

    def add_device(self, body):
        name, host = self.name(body['name']), plc_ipv4(body['host'])
        with self.db() as db:
            if not db.execute('SELECT 1 FROM warehouses WHERE id=?',(body['warehouse_id'],)).fetchone():
                raise WmsError('请先创建或选择仓库')
            self._unique_endpoint(db,host,body['port'])
            if db.execute('SELECT 1 FROM devices WHERE warehouse_id=? AND name=?',(body['warehouse_id'],name)).fetchone():
                raise WmsError('该仓库中已存在同名设备')
            ident = secrets.token_hex(16)
            folder = self.folder / 'devices' / ident
            folder.mkdir(parents=True)
            base = self.apps['primary'].state.config
            config = {**base, **{k:body[k] for k in ('port','rack','slot','inbound_dock','outbound_dock')},
                      'host':host,'control_enabled':False,'initial_locations':[], 'device_revision':0}
            config.pop('device_updated_at',None)
            config.pop('device_enabled',None)
            save_config(folder/'wms.json',config)
            child = self._load(ident)
            # New devices start disabled, with an empty independent ledger.
            db.execute('INSERT INTO devices VALUES(?,?,?,1)',(ident,body['warehouse_id'],name))
            return dict(id=ident,warehouse_id=body['warehouse_id'],name=name,revision=1,enabled=False)

    @staticmethod
    def _safe(service):
        ledger=service.store.read()
        if any(t['status'] not in {'COMPLETED','CANCELLED'} for t in ledger['tasks']):
            raise WmsError('设备仍有未结束任务，请完成或取消后再修改连接或启停')
        if any(s['status'] in {'AT_STATION','AT_EXIT'} for s in ledger['stock'].values()):
            raise WmsError('出入口仍有料箱，请完成交接或返库后再修改')
        if service.reader.snapshot().get('connected'):
            service.reader.poll()
        sample=service.reader.snapshot()
        if feedback(sample).get('DB2.DBW0') not in (None,8) or sample.get('command_code',0) or sample.get('idle_ack',0):
            raise WmsError('PLC 尚未空闲或回执未复位，不能修改连接或启停')

    def update_device(self, ident, body):
        name, host = self.name(body['name']), plc_ipv4(body['host'])
        with self.db() as db:
            row=db.execute('SELECT * FROM devices WHERE id=?',(ident,)).fetchone()
            if not row or row['revision'] != body['expected_revision']:
                raise WmsError('设备资料已改变，请刷新后重试')
            self._unique_endpoint(db,host,body['port'],ident)
            if db.execute('SELECT 1 FROM devices WHERE warehouse_id=? AND name=? AND id!=?',(row['warehouse_id'],name,ident)).fetchone():
                raise WmsError('该仓库中已存在同名设备')
            app=self.apps[ident];service=app.state.service;reader=service.reader;settings=app.state.device
            config=app.state.config
            primary_config=self.apps['primary'].state.config
            if body['enabled'] and (primary_config.get('profile')!='PHYSICAL_CONTROL' or primary_config.get('control_enabled') is not True):
                raise WmsError('后台全局写入未启用，不能启用设备控制')
            enable_key='device_enabled' if ident=='primary' else 'control_enabled'
            changes={**{k:body[k] for k in ('port','rack','slot','inbound_dock','outbound_dock')},'host':host,enable_key:body['enabled']}
            defaults={'port':102,'rack':0,'slot':1,'device_enabled':True}
            changed=any(config.get(k,defaults.get(k)) != v for k,v in changes.items())
            with service.lock,reader.lock,service.store.lock:
                if changed:
                    if not settings.snapshot()['editable']:
                        raise WmsError('当前设备未配置可保存的连接文件')
                    if json.loads(settings.path.read_text(encoding='utf-8-sig')) != config:
                        raise WmsError('设备配置已被外部修改，请重启后台后重试')
                    self._safe(service)
                    next_config={**config,**changes,'device_revision':config.get('device_revision',0)+1,'device_updated_at':now()}
                    replacement=WmsReader(next_config)
                    # Prepare the command journal before saving settings; it belongs to this device only.
                    control=service.control or (ManualControl(reader,self.commands[ident]) if body['enabled'] else None)
                    save_config(settings.path,next_config)
                    config.update(next_config)
                    reader.replace_connection(replacement,config)
                    service.enabled=body['enabled']
                    service.auto_acknowledge=bool(service.enabled and config.get('auto_acknowledge',True))
                    service.control=control
                    settings._publish()
                db.execute('UPDATE devices SET name=?,revision=revision+1 WHERE id=?',(name,ident))
            return dict(db.execute('SELECT * FROM devices WHERE id=?',(ident,)).fetchone())

    def change_host(self, ident, host, revision):
        with self.db() as db:
            app=self.apps[ident]
            self._unique_endpoint(db,plc_ipv4(host),app.state.config.get('port',102),ident)
            result=app.state.device.change_host(host,revision)
            if result['changed']:
                db.execute('UPDATE devices SET revision=revision+1 WHERE id=?',(ident,))
            return result

    def _start_worker(self, ident):
        if self.closing or ident in self.workers:
            return
        async def sample():
            service=self.apps[ident].state.service
            while True:
                try:
                    if service.enabled:
                        await asyncio.to_thread(service.poll)
                except Exception:
                    import logging
                    logging.exception('WMS device %s feedback failed; no command retry',ident)
                await asyncio.sleep(1)
        self.workers[ident]=asyncio.create_task(sample())

    async def start(self, automatic):
        self.closing=False;self.workers={}
        self.loop=asyncio.get_running_loop();self.automatic=automatic
        for ident,app in self.apps.items():
            if ident!='primary':
                app.state.store.recover()
            if automatic:
                self._start_worker(ident)

    async def close(self):
        self.closing=True
        for worker in self.workers.values():worker.cancel()
        await asyncio.gather(*self.workers.values(),return_exceptions=True)
        def close(app):
            service=app.state.service
            with service.lock,service.reader.lock:service.reader.close()
        await asyncio.gather(*(asyncio.to_thread(close,app) for app in self.apps.values()))
