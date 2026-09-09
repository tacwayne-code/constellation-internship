from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    database_url: str
    identity_db: Path
    legacy_crm_json: Path
    legacy_ass_db: Path
    admin_username: str
    admin_password: str
    admin_password_hash: str
    admin_session_secret: str
    cookie_secure: bool
    wechat_app_id: str
    wechat_app_secret: str
    sso_shared_secret: str
    odoo_base_url: str
    odoo_database: str
    odoo_username: str
    odoo_api_key: str
    odoo_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=os.getenv("PLATFORM_DATABASE_URL", "sqlite:///./platform.db"),
            identity_db=_path("LEGACY_IDENTITY_DB", "./identity.db"),
            legacy_crm_json=_path("LEGACY_CRM_JSON", "./shared-db.json"),
            legacy_ass_db=_path("LEGACY_ASS_DB", "./aftersales.db"),
            admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
            admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", "").strip(),
            admin_session_secret=os.getenv("ADMIN_SESSION_SECRET", "").strip(),
            cookie_secure=os.getenv("COOKIE_SECURE", "1").lower() in {"1", "true", "yes"},
            wechat_app_id=os.getenv("WECHAT_APP_ID", "").strip(),
            wechat_app_secret=os.getenv("WECHAT_APP_SECRET", "").strip(),
            sso_shared_secret=os.getenv("SSO_SHARED_SECRET", "").strip(),
            odoo_base_url=os.getenv("ODOO_BASE_URL", "").rstrip("/"),
            odoo_database=os.getenv("ODOO_DATABASE", "").strip(),
            odoo_username=os.getenv("ODOO_USERNAME", "").strip(),
            odoo_api_key=os.getenv("ODOO_API_KEY", os.getenv("ODOO_PASSWORD", "")).strip(),
            odoo_timeout_seconds=int(os.getenv("ODOO_TIMEOUT_SECONDS", "20")),
        )

    @property
    def admin_ready(self) -> bool:
        return bool(self.admin_username and (self.admin_password_hash or self.admin_password) and self.admin_session_secret)

    @property
    def wechat_ready(self) -> bool:
        return bool(self.wechat_app_id and self.wechat_app_secret and self.sso_shared_secret)

    @property
    def odoo_ready(self) -> bool:
        return bool(self.odoo_base_url and self.odoo_database and self.odoo_username and self.odoo_api_key)


settings = Settings.from_environment()
