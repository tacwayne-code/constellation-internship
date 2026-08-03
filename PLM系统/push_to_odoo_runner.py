"""独立进程跑 Odoo 推送任务，避免主进程 SQLite 锁"""
import sys, os, sqlite3 as sq, time, logging, socket
from datetime import datetime

BOM_ID = int(sys.argv[1])
CFG_ID = int(sys.argv[2])
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = sys.argv[3] if len(sys.argv) > 3 else os.path.join(_BASE_DIR, 'plm_push.log')
DB_PATH = sys.argv[4] if len(sys.argv) > 4 else os.path.join(_BASE_DIR, 'plm.db')

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

def fail(msg):
    log.error(f'Push FAIL bom={BOM_ID}: {msg}')
    try:
        con = sq.connect(DB_PATH, timeout=30)
        con.execute('UPDATE plm_bom SET sync_status=?, sync_message=?, sync_time=? WHERE id=?',
                    ('sync_failed', msg[:200], datetime.now().isoformat(), BOM_ID))
        con.commit(); con.close()
    except Exception as e:
        log.error(f'write failure: {e}')

try:
    time.sleep(2)
    log.info(f'Sub-process start bom={BOM_ID} cfg={CFG_ID}')

    import xmlrpc.client, ssl
    ctx = ssl.create_default_context()
    # 默认验证 SSL 证书；设置 PLM_ODOO_INSECURE_SSL=1 可跳过（仅内网测试用）
    if os.environ.get('PLM_ODOO_INSECURE_SSL') == '1':
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    socket.setdefaulttimeout(180)

    con = sq.connect(DB_PATH, isolation_level=None, timeout=60)
    cur = con.cursor()
    cur.execute('SELECT api_url, db_name, username, api_key FROM plm_integration_config WHERE id=? AND is_active=1', (CFG_ID,))
    cfg = cur.fetchone()
    if not cfg:
        con.close()
        fail('integration config not found or inactive')
        sys.exit(1)
    log.info(f'cfg loaded: {cfg[0]}')

    cur.execute('SELECT p.code, p.name FROM plm_bom b LEFT JOIN plm_product p ON b.product_id=p.id WHERE b.id=?', (BOM_ID,))
    bom_row = cur.fetchone()
    cur.execute('SELECT p.code, p.name, bi.quantity FROM plm_bom_item bi LEFT JOIN plm_product p ON bi.product_id=p.id WHERE bi.bom_id=? AND p.id IS NOT NULL ORDER BY bi.seq', (BOM_ID,))
    items = cur.fetchall()
    log.info(f'bom_row={bom_row}, items count={len(items)}')
    cur.execute('SELECT bom_no FROM plm_bom WHERE id=?', (BOM_ID,))
    bom_no = cur.fetchone()[0]

    base = cfg[0].rstrip('/')
    common = xmlrpc.client.ServerProxy(f'{base}/xmlrpc/2/common', context=ctx)
    log.info(f'Calling authenticate to {base}')
    uid = common.authenticate(cfg[1], cfg[2], cfg[3], {})
    log.info(f'auth uid={uid}')
    if not uid:
        con.close()
        fail('Odoo auth failed')
        sys.exit(1)

    models = xmlrpc.client.ServerProxy(f'{base}/xmlrpc/2/object', context=ctx)

    def call(model, method, *args, retries=8):
        """执行 Odoo RPC，自动重试 SerializationFailure / ProtocolError"""
        global uid
        for attempt in range(retries):
            try:
                return models.execute_kw(cfg[1], uid, cfg[3], model, method, list(args))
            except xmlrpc.client.Fault as f:
                err = str(f)
                if 'SerializationFailure' in err or 'concurrent update' in err or 'reload' in err.lower():
                    wait = 2 * (attempt + 1)
                    log.warning(f'{model}.{method}: retry {attempt+1}/{retries} after {wait}s (SerializationFailure)')
                    time.sleep(wait)
                    try:
                        new_uid = common.authenticate(cfg[1], cfg[2], cfg[3], {})
                        if new_uid:
                            uid = new_uid
                    except Exception:
                        pass
                    continue
                raise
            except (xmlrpc.client.ProtocolError, ConnectionError, OSError) as e:
                wait = min(30, 3 * (attempt + 1))
                log.warning(f'{model}.{method}: retry {attempt+1}/{retries} after {wait}s (Network: {e})')
                time.sleep(wait)
                try:
                    new_uid = common.authenticate(cfg[1], cfg[2], cfg[3], {})
                    if new_uid:
                        uid = new_uid
                except Exception:
                    pass
                continue
        raise Exception(f'{model}.{method} failed after {retries} retries')

    # 直接调用 helper：不经过 call() 包装，避免 args 多层嵌套
    def rpc(model, method, *args):
        return models.execute_kw(cfg[1], uid, cfg[3], model, method, list(args))

    pid = 0
    if bom_row and bom_row[0]:
        pids = rpc('product.template', 'search', [['default_code', '=', bom_row[0]]])
        pid = pids[0] if pids else 0
        if not pid:
            pid = rpc('product.template', 'create', {'name': bom_row[1], 'default_code': bom_row[0]})
    log.info(f'main product pid={pid}')

    oid = rpc('mrp.bom', 'create', {'product_tmpl_id': pid, 'code': bom_no, 'type': 'normal'})
    log.info(f'mrp.bom created id={oid}')

    n = 0
    for item in items:
        sids = rpc('product.product', 'search', [['default_code', '=', item[0]]])
        sid = sids[0] if sids else 0
        if not sid:
            t = rpc('product.template', 'create', {'name': item[1], 'default_code': item[0]})
            sids = rpc('product.product', 'search', [['product_tmpl_id', '=', t]])
            sid = sids[0] if sids else 0
        if sid:
            rpc('mrp.bom.line', 'create', {'bom_id': oid, 'product_id': sid, 'product_qty': item[2] or 1})
            n += 1

    msg = f'已推 {n} 项到 Odoo (BOM #{oid})'
    log.info(msg)
    cur.execute('UPDATE plm_bom SET sync_status=?, sync_message=?, sync_time=? WHERE id=?',
                ('synced', msg, datetime.now().isoformat(), BOM_ID))
    cur.execute('INSERT INTO plm_sync_log (integration_id,direction,status,records_count,message,created_at) VALUES (?,?,?,?,?,?)',
                (CFG_ID, 'export', 'success', n, f'BOM {bom_no}: {n} items', datetime.now().isoformat()))
    con.commit()
    con.close()
    log.info(f'DONE. bom={BOM_ID} pushed={n}')
except Exception as e:
    log.exception(f'Exception: {e}')
    fail(f'{type(e).__name__}: {e}')
