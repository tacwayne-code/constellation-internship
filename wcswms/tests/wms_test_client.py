"""Authenticated administrator fixture for existing WMS business regressions.

Security tests use FastAPI's raw TestClient to exercise the actual login boundary.
This fixture seeds an isolated test account, then obtains a real cookie/CSRF token.
"""
from fastapi.testclient import TestClient as AnonymousClient

PASSWORD = 'isolated-regression-password'

def TestClient(app, **kwargs):
    if not app.state.users.initialized():
        app.state.users.create('regression-admin', '回归测试管理员', PASSWORD, 'admin', [], '测试夹具', bootstrap=True)
    client = AnonymousClient(app, **kwargs)
    login = client.post('/api/auth/login', json={'username': 'regression-admin', 'password': PASSWORD},
                        headers={'X-WMS-Request': '1'})
    assert login.status_code == 200, login.text
    client.headers['X-CSRF-Token'] = login.json()['csrf_token']
    client.headers['X-WMS-Request'] = '1'
    return client
