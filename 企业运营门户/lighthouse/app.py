import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from lighthouse.settings import Settings, ROOT
from lighthouse.odoo import ReadOnlyOdoo, BoardClient, OrderClient
from lighthouse.snapshots import SnapshotStore
from lighthouse.adapters import inventory, urgent, orders

BUILDERS = {'inventory': inventory.build, 'urgent': urgent.build, 'orders': orders.build}


def create_app(settings=None, transport=None, start_collector=True):
    settings = settings or Settings.from_env()
    transport = transport or ReadOnlyOdoo(settings)
    store = SnapshotStore(settings.configured, settings.refresh)

    def load(key):
        client = (OrderClient if key == 'orders' else BoardClient)(transport)
        data = BUILDERS[key](client)
        # Never leak account identity and internal server details via legacy metadata.
        data['meta'] = {k: v for k, v in data.get('meta', {}).items() if k in ('range','accuracyNote','progressNote')}
        data['meta']['source'] = 'odoo'
        return data, client.issues

    async def collect():
        while True:
            # One worker per application; browsers only read snapshots.
            for key in BUILDERS:
                await asyncio.to_thread(store.collect, key, lambda key=key: load(key))
            await asyncio.sleep(settings.refresh)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(collect()) if settings.configured and start_collector else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        transport.close()

    app = FastAPI(title='灯塔', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.load = load

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if request.method not in ('GET', 'HEAD'):
            response = JSONResponse({'error': '灯塔仅提供只读查询'}, status_code=405, headers={'Allow':'GET, HEAD'})
        elif request.url.path.startswith('/api/') and settings.token and not hmac.compare_digest(
                request.headers.get('Authorization',''), 'Bearer ' + settings.token):
            response = JSONResponse({'error':'请输入展示访问口令'}, status_code=401)
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'none'; object-src 'none'"
        return response

    @app.get('/api/boards/{key}')
    def board(key: str):
        if key not in BUILDERS:
            return JSONResponse({'error':'看板不存在'}, status_code=404)
        return store.get(key)

    @app.get('/api/status')
    def status():
        return {key: {k:v for k,v in store.get(key).items() if k != 'data'} for key in BUILDERS}

    @app.get('/healthz')
    def health():
        return {'status':'running', 'readOnly':True}

    @app.get('/readyz')
    def ready():
        complete = all(store.get(key)['status'] == 'live' for key in BUILDERS)
        return JSONResponse({'ready':complete}, status_code=200 if complete else 503)

    # Explicit assets only. Never expose source, environment files, or arbitrary directories.
    assets = {'': 'index.html', 'index.html':'index.html', 'shell.css':'shell.css', 'shell.js':'shell.js',
              'shared.js':'shared.js', 'shared.css':'shared.css'}
    for key in BUILDERS:
        assets[f'boards/{key}/'] = f'boards/{key}/index.html'
        for filename in ('index.html','app.js','styles.css'):
            assets[f'boards/{key}/{filename}'] = f'boards/{key}/{filename}'

    @app.get('/{path:path}')
    def asset(path: str):
        if path not in assets:
            return JSONResponse({'error':'页面不存在'}, status_code=404)
        return FileResponse(ROOT / 'web' / assets[path])

    return app
