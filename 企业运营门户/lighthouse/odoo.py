"""Only this module talks to Odoo. No generic RPC endpoint is exposed to browsers."""
import copy
import json
import threading
import time
import httpx


class OdooError(RuntimeError):
    pass


READ_METHODS = frozenset({'search_read', 'search_count', 'read', 'fields_get', 'search', 'name_search'})
MODELS = frozenset({'purchase.order', 'purchase.order.line', 'stock.quant', 'stock.location',
    'stock.warehouse', 'stock.picking', 'stock.picking.type', 'stock.move', 'stock.move.line',
    'stock.warehouse.orderpoint', 'product.product', 'product.template', 'product.supplierinfo',
    'res.partner', 'res.currency', 'sale.order', 'sale.order.line', 'mrp.production', 'mrp.workorder'})


class ReadOnlyOdoo:
    def __init__(self, settings, http=None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=settings.timeout, follow_redirects=False, trust_env=False)
        self.lock = threading.RLock()
        self.uid = None
        self.cache = {}

    def close(self):
        self.http.close()

    def _rpc(self, path, params):
        try:
            response = self.http.post(self.settings.url + path, json={'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OdooError('Odoo 连接失败或响应无效') from exc
        if not isinstance(body, dict) or body.get('error') or 'result' not in body:
            self.uid = None
            raise OdooError('Odoo 拒绝读取；请检查只读账号权限和字段配置')
        return body['result']

    def authenticate(self):
        if not self.settings.configured:
            raise OdooError('尚未配置 Odoo 只读连接')
        with self.lock:
            if not self.uid:
                data = self._rpc('/web/session/authenticate', {'db': self.settings.database,
                    'login': self.settings.username, 'password': self.settings.password})
                if not isinstance(data, dict) or not data.get('uid'):
                    raise OdooError('Odoo 只读账号认证失败')
                self.uid = data['uid']
            return {'uid': self.uid}

    def call(self, model, method, args=None, kwargs=None):
        # Check before authentication or any network activity.
        if model not in MODELS or method not in READ_METHODS:
            raise OdooError('灯塔禁止此模型或非只读操作')
        args, kwargs = args or [], kwargs or {}
        key = json.dumps([model, method, args, kwargs], sort_keys=True, ensure_ascii=False)
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 20:
                return copy.deepcopy(cached[1])
            self.authenticate()
            data = self._rpc('/web/dataset/call_kw', {'model': model, 'method': method, 'args': args, 'kwargs': kwargs})
            if len(self.cache) > 512:
                self.cache.clear()
            self.cache[key] = (time.monotonic(), copy.deepcopy(data))
            return data


class BoardClient:
    """Legacy display adapters share transport; incomplete reads remain visible."""
    def __init__(self, transport):
        self.transport = transport
        self.issues = set()
        self.deadline = time.monotonic() + 90

    def authenticate(self):
        return self.transport.authenticate()

    def call_kw(self, model, method, args=None, kwargs=None):
        try:
            if time.monotonic() > self.deadline:
                raise OdooError('本轮采集超时，等待下轮恢复')
            return self.transport.call(model, method, args, kwargs)
        except OdooError:
            self.issues.add(model + ': 读取不完整')
            raise

    call = call_kw

    def search_read(self, model, fields, domain=None, limit=100, order=None):
        kwargs = {'fields': fields, 'limit': limit}
        if order:
            kwargs['order'] = order
        rows = self.call_kw(model, 'search_read', [domain or []], kwargs)
        if len(rows) >= limit:
            self.issues.add(model + ': 达到显示读取上限')
        return rows

    def search_read_all(self, model, fields, domain=None, order=None, page_size=500, max_rows=None):
        rows = []
        maximum = max_rows or 10000
        while len(rows) < maximum:
            size = min(page_size, maximum - len(rows))
            kwargs = {'fields': fields, 'limit': size, 'offset': len(rows), 'order': order or 'id asc'}
            page = self.call_kw(model, 'search_read', [domain or []], kwargs)
            rows.extend(page)
            if len(page) < size:
                return rows
        self.issues.add(model + ': 达到读取上限')
        return rows

    def search_count(self, model, domain=None):
        return self.call_kw(model, 'search_count', [domain or []])

    def read(self, model, ids, fields):
        return self.call_kw(model, 'read', [ids], {'fields': fields}) if ids else []


class OrderClient(BoardClient):
    def search_read(self, model, domain, fields, limit=100, order=None):
        return super().search_read(model, fields, domain, limit, order)
