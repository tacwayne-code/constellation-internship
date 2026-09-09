import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    return values


def load_env():
    # Read CRM's private configuration without changing it or importing its write APIs.
    values = read_env(ROOT / '.env')
    shared = os.environ.get('LIGHTHOUSE_CRM_ENV_FILE') or values.get('LIGHTHOUSE_CRM_ENV_FILE')
    if shared:
        path = Path(shared).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise ValueError('LIGHTHOUSE_CRM_ENV_FILE 指向的配置文件不存在')
        crm = read_env(path)
        for key in ('ODOO_BASE_URL', 'ODOO_DATABASE', 'ODOO_USERNAME', 'ODOO_PASSWORD', 'ODOO_TIMEOUT_SECONDS'):
            if crm.get(key) and not values.get(key):
                values[key] = crm[key]
    return {**values, **os.environ}


@dataclass(frozen=True)
class Settings:
    url: str = ''
    database: str = ''
    username: str = ''
    password: str = ''
    token: str = ''
    refresh: int = 180
    timeout: int = 12
    host: str = '127.0.0.1'
    port: int = 8780

    @property
    def configured(self):
        return all((self.url, self.database, self.username, self.password))

    @classmethod
    def from_env(cls):
        env = load_env()
        result = cls(url=(env.get('ODOO_URL') or env.get('ODOO_BASE_URL', '')).rstrip('/'),
                     database=env.get('ODOO_DB') or env.get('ODOO_DATABASE', ''),
                     username=env.get('ODOO_USER') or env.get('ODOO_USERNAME', ''),
                     password=env.get('ODOO_PASSWORD', ''), token=env.get('LIGHTHOUSE_TOKEN', ''),
                     refresh=int(env.get('LIGHTHOUSE_REFRESH_SECONDS', '180')),
                     timeout=int(env.get('ODOO_TIMEOUT_SECONDS', '12')),
                     host=env.get('LIGHTHOUSE_HOST', '127.0.0.1'),
                     port=int(env.get('LIGHTHOUSE_PORT', '8780')))
        if result.url:
            parsed = urlsplit(result.url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('ODOO_URL 必须为不含凭证的 HTTP(S) 地址')
        if not 30 <= result.refresh <= 3600 or not 2 <= result.timeout <= 60:
            raise ValueError('刷新间隔应为 30–3600 秒，请求超时应为 2–60 秒')
        if result.host not in ('127.0.0.1', 'localhost', '::1') and not result.token:
            raise ValueError('局域网开放前必须设置 LIGHTHOUSE_TOKEN')
        return result
