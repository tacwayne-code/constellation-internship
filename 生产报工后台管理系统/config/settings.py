"""Django settings for the production-reporting administration service."""

import os
from pathlib import Path

import pymysql

pymysql.install_as_MySQLdb()

BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file():
    """Load local development values without overriding process environment."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


load_env_file()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = required_env("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in required_env("DJANGO_ALLOWED_HOSTS").split(",") if host.strip()]

INSTALLED_APPS = [
    "simpleui",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "reports.apps.ReportsConfig",
    "employees",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.mysql",
    "NAME": required_env("MYSQL_DATABASE"),
    "USER": required_env("MYSQL_USER"),
    "PASSWORD": required_env("MYSQL_PASSWORD"),
    "HOST": required_env("MYSQL_HOST"),
    "PORT": os.environ.get("MYSQL_PORT", "3306"),
    "OPTIONS": {"charset": "utf8mb4"},
}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
SIMPLEUI_DEFAULT_THEME = "element-ui"
SIMPLEUI_LANGUAGE = "zh-hans"
# Keep the admin landing page focused on application shortcuts.
SIMPLEUI_HOME_INFO = False
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INTERNAL_REPORT_API_KEY = required_env("INTERNAL_REPORT_API_KEY")

# Read-only SOP HTTP source. The management system never accesses SOP SQLite.
SOP_REPORTS_API_URL = os.environ.get(
    "SOP_REPORTS_API_URL",
    "http://192.168.1.100:8093/api/reports",
).strip()
SOP_WORKORDERS_API_URL = os.environ.get(
    "SOP_WORKORDERS_API_URL",
    "http://192.168.1.100:8093/api/workorders",
).strip()
SOP_ORDER_SUMMARY_API_URL = os.environ.get(
    "SOP_ORDER_SUMMARY_API_URL",
    "http://192.168.1.100:8093/api/order-summary",
).strip()
SOP_WORKERS_API_URL = os.environ.get(
    "SOP_WORKERS_API_URL",
    "http://192.168.1.100:8093/api/workers",
).strip()
SOP_EMPLOYEE_SYNC_URL = os.environ.get(
    "SOP_EMPLOYEE_SYNC_URL",
    "http://192.168.1.100:8093/api/workers/sync",
).strip()
SOP_REPORTS_SYNC_INTERVAL = max(
    10,
    int(os.environ.get("SOP_REPORTS_SYNC_INTERVAL", "30")),
)

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
