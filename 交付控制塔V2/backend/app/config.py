"""应用配置（Pydantic Settings，环境变量加载）"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Odoo ----
    ODOO_URL: str = "http://192.168.1.100:8018"      # 后端 XML-RPC 连接地址（服务器视角，同机可用 127.0.0.1）
    ODOO_WEB_URL: str = ""                            # 前端「在 Odoo 打开」跳转地址（客户端浏览器可达地址；留空则回退 ODOO_URL）
    ODOO_DB: str = "inspiri_erp_test"
    ODOO_USER: str = "admin"
    ODOO_PASSWORD: str = ""
    SESSION_TTL: int = 1800  # Odoo 会话有效时长（秒）

    # ---- Mock 降级 ----
    USE_MOCK: bool = False            # 全局强制 Mock
    MOCK_DATA_DIR: str = "mock_data"  # Mock JSON 数据目录

    # ---- PLM 适配器 ----
    PLM_ADAPTER: str = "mock"         # mock / rest / 自定义 key
    PLM_URL: str = ""
    PLM_API_KEY: str = ""
    PLM_MODEL: str = ""

    # ---- 缓存 ----
    CACHE_TTL_DEFAULT: int = 60       # 默认缓存秒数
    REDIS_URL: str = ""               # 留空则用内存缓存

    # ---- Web ----
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    API_PREFIX: str = "/api"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
