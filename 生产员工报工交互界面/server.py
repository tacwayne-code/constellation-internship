import json
import logging
import math
import os
import re
import signal
import sqlite3
import threading
import time
import uuid
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import odoo_adapter
from odoo_remaining_qty_fix import OdooRemainingQuantityFix

# ---- 加载 .env 文件 ----
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip()
                if _key not in os.environ:
                    os.environ[_key] = _val
    del _key, _val, _line, _f

# ============================================================
# 配置 & 常量
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "ai_test")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
MOCK_MODE = os.getenv("ODOO_MOCK_MODE", "false").lower() == "true"
LOCAL_TZ = timezone(timedelta(hours=8))
API_KEY = os.getenv("API_KEY", "").strip()
DB_FILE = BASE_DIR / ("data.mock.db" if MOCK_MODE else "data.db")
RESET_MARKER_FILE = BASE_DIR / ".reset.marker"
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}


def reset_marker_stamp():
    """Return the cross-process reset marker timestamp."""
    try:
        return RESET_MARKER_FILE.stat().st_mtime_ns
    except OSError:
        return 0

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("production-dashboard")
if MOCK_MODE:
    logger.warning("=" * 60)
    logger.warning("ODOO MOCK MODE ENABLED - NO REAL ODOO WRITES")
    logger.warning("=" * 60)
else:
    logger.info(f"Odoo: {ODOO_URL}")
    logger.debug(f"Odoo 连接详情: db={ODOO_DB} user={ODOO_USER}")

# ============================================================
# 并发锁
# ============================================================

DB_LOCK = threading.Lock()
ODOO_LOCK = threading.Lock()
MATERIAL_QUANTITY_LOCK = threading.Lock()

# ============================================================
# Odoo 客户端
# ============================================================

class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self._uid = None
        import socket
        # 设置 30 秒超时避免 Odoo 挂死时阻塞（单进程服务，无副作用）
        if not hasattr(OdooClient, "_timeout_set"):
            socket.setdefaulttimeout(30)
            OdooClient._timeout_set = True
        self.common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
        )
        self.models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
        )

    def authenticate(self):
        if not ODOO_PASSWORD:
            raise OdooError("缺少 ODOO_PASSWORD")
        uid = self.common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise OdooError("Odoo 登录失败")
        self._uid = uid
        return uid

    def call(self, model, method, args=None, kwargs=None):
        with ODOO_LOCK:
            if self._uid is None:
                self.authenticate()
            return self.models.execute_kw(
                ODOO_DB, self._uid, ODOO_PASSWORD, model, method,
                args or [], kwargs or {},
            )

    def search_read(self, model, domain, fields, limit=100, order=None):
        kw = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return self.call(model, "search_read", [domain], kw)

    def read(self, model, ids, fields):
        if not ids:
            return []
        return self.call(model, "read", [ids], {"fields": fields})


# ============================================================
# 工厂函数 - 根据 Mock 模式选择 Odoo 客户端
# ============================================================

_odoo_client = None
_odoo_mode = None

def get_odoo():
    global _odoo_client, _odoo_mode
    if _odoo_client is None:
        _odoo_client, _odoo_mode, _ = odoo_adapter.create_odoo_client(
            OdooClient, OdooError
        )
        if _odoo_mode == "mock":
            logger.warning("FakeOdooClient 已激活 - 模拟模式")
    return _odoo_client

def get_odoo_mode():
    global _odoo_mode
    if _odoo_mode is None:
        get_odoo()
    return _odoo_mode or ("mock" if MOCK_MODE else "real")


def odoo_call(client, model, method, args=None, kwargs=None):
    """Call either the project client wrapper or an execute_kw-compatible client."""
    call = getattr(client, "call", None)
    if callable(call):
        return call(model, method, args, kwargs)
    execute_kw = getattr(client, "execute_kw", None)
    if callable(execute_kw):
        uid = getattr(client, "_uid", None)
        if uid is None and hasattr(client, "authenticate"):
            uid = client.authenticate()
        if uid is None:
            raise OdooError("Odoo client is not authenticated")
        return execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, model, method,
            args or [], kwargs or {},
        )
    raise TypeError("Unsupported Odoo client: missing call/execute_kw")

# ============================================================
# SQLite 数据层
# ============================================================

def _migrate_db():
    """可重复执行的 SQLite 迁移"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # workers 表迁移
        existing_wcols = {row[1] for row in cursor.execute("PRAGMA table_info(workers)").fetchall()}
        worker_migrations = [
            ("source", "TEXT DEFAULT 'local'"),
            ("odoo_employee_id", "INTEGER DEFAULT 0"),
            ("operation_codes", "TEXT DEFAULT '[]'"),
        ]
        for col_name, col_def in worker_migrations:
            if col_name not in existing_wcols:
                cursor.execute(f"ALTER TABLE workers ADD COLUMN {col_name} {col_def}")
                logger.info(f"迁移: workers 表新增字段 {col_name}")

        # reports 表迁移
        existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(reports)").fetchall()}

        migrations = [
            ("production_id", "TEXT DEFAULT ''"),
            ("workorder_id", "TEXT DEFAULT ''"),
            ("odoo_employee_id", "INTEGER DEFAULT 0"),
            ("idempotency_key", "TEXT DEFAULT ''"),
            ("odoo_report_id", "TEXT DEFAULT ''"),
            ("odoo_stock_move_ids", "TEXT DEFAULT ''"),
            ("sync_status", "TEXT DEFAULT 'local'"),
            ("material_sync_status", "TEXT DEFAULT 'unknown'"),
            ("odoo_progress_qty", "REAL DEFAULT NULL"),
            ("error_message", "TEXT DEFAULT ''"),
        ]

        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_def}")
                logger.info(f"迁移: reports 表新增字段 {col_name}")

        # 创建 report_materials 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS report_materials (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id  TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                bom_line_id INTEGER DEFAULT 0,
                default_code TEXT DEFAULT '',
                actual_qty  REAL NOT NULL CHECK(actual_qty > 0),
                uom_id     INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (report_id) REFERENCES reports(id)
            )
        """)

        # idempotency_key 唯一索引
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_idempotency
            ON reports(idempotency_key)
            WHERE idempotency_key != ''
        """)

        conn.commit()
        conn.close()
    logger.info(f"SQLite 迁移完成 (DB: {DB_FILE})")


def _init_db():
    """初始化 SQLite 数据库和表结构"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workers (
                id    TEXT PRIMARY KEY,
                name  TEXT NOT NULL,
                team  TEXT DEFAULT '',
                source TEXT DEFAULT 'local',
                odoo_employee_id INTEGER DEFAULT 0,
                operation_codes TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id         TEXT PRIMARY KEY,
                worker_id  TEXT NOT NULL,
                worker_name TEXT NOT NULL,
                worker_team TEXT DEFAULT '',
                order_id   TEXT NOT NULL,
                order_customer TEXT DEFAULT '',
                order_product  TEXT DEFAULT '',
                operation  TEXT NOT NULL,
                operation_label TEXT NOT NULL,
                qty        INTEGER NOT NULL CHECK(qty > 0),
                qualified  INTEGER NOT NULL DEFAULT 0,
                hours      REAL NOT NULL DEFAULT 0,
                remark     TEXT DEFAULT '',
                date       TEXT NOT NULL,
                time       TEXT NOT NULL,
                timestamp  INTEGER NOT NULL,
                production_id TEXT DEFAULT '',
                workorder_id  TEXT DEFAULT '',
                 odoo_employee_id INTEGER DEFAULT 0,
                 idempotency_key TEXT DEFAULT '',
                 odoo_report_id  TEXT DEFAULT '',
                 odoo_stock_move_ids TEXT DEFAULT '',
                 sync_status TEXT DEFAULT 'local',
                 material_sync_status TEXT DEFAULT 'unknown',
                 odoo_progress_qty REAL DEFAULT NULL,
                 error_message TEXT DEFAULT '',
                 created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
            CREATE INDEX IF NOT EXISTS idx_reports_worker_date ON reports(worker_id, date);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_worker_date
                ON reports(worker_id, order_id, date, operation);

            -- ESOP：SOP 查看日志
            CREATE TABLE IF NOT EXISTS sop_view_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id  TEXT NOT NULL,
                attachment_name TEXT DEFAULT '',
                worker_id      TEXT DEFAULT '',
                worker_name    TEXT DEFAULT '',
                workorder_id   TEXT DEFAULT '',
                viewed_at      TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_sop_logs_wo ON sop_view_logs(workorder_id);
        """)
        conn.commit()
        conn.close()
    logger.info("SQLite 数据库初始化完成")
    # 执行迁移
    _migrate_db()


def _seed_workers():
    """首次启动时写入默认工人（含罗伟华）"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        count = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        if count == 0:
            default = [
                ("WK001", "张建国", "A班", "local", 0, '["assembly"]'),
                ("WK002", "周明辉", "A班", "local", 0, '["packing"]'),
                ("WK003", "王志强", "B班", "local", 0, '["test_tape_operation"]'),
                ("WK004", "陈晓峰", "B班", "local", 0, '["test_splitter_operation"]'),
                ("WK005", "刘大伟", "C班", "local", 0, '["test_assembly_operation"]'),
                ("WK006", "赵永刚", "夜班", "local", 0, '["test_packing_operation"]'),
                ("LOCAL_LWH", "罗伟华", "组装班", "local", 0,
                 '["pc_assembly_tape", "pc_assembly_splitter"]'),
            ]
            conn.executemany(
                "INSERT INTO workers "
                "(id, name, team, source, odoo_employee_id, operation_codes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                default
            )
            conn.commit()
            logger.info("已写入 7 个默认工人（含罗伟华）")
        else:
            # 确保罗伟华存在（如果没有的话）
            existing = conn.execute("SELECT id FROM workers WHERE name = '罗伟华'").fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO workers "
                    "(id, name, team, source, odoo_employee_id, operation_codes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("LOCAL_LWH", "罗伟华", "组装班", "local", 0,
                     '["pc_assembly_tape", "pc_assembly_splitter"]')
                )
                conn.commit()
                logger.info("已添加罗伟华（本地工人）")
        conn.close()
    _ensure_worker_operation_bindings()


WORKER_OPERATION_BINDINGS = {
    "LOCAL_LWH": ["pc_assembly_tape", "pc_assembly_splitter"],
    "WK001": ["assembly"],
    "WK002": ["packing"],
    "WK003": ["test_tape_operation"],
    "WK004": ["test_splitter_operation"],
    "WK005": ["test_assembly_operation"],
    "WK006": ["test_packing_operation"],
}

def _ensure_worker_operation_bindings():
    """Persist the allowed operation codes for configured local workers."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        for worker_id, operation_codes in WORKER_OPERATION_BINDINGS.items():
            encoded = json.dumps(operation_codes, ensure_ascii=False)
            conn.execute(
                "UPDATE workers SET operation_codes=? "
                "WHERE id=? AND (operation_codes IS NULL OR trim(operation_codes) IN ('', '[]'))",
                (encoded, worker_id),
            )
        conn.commit()
        conn.close()


def db_workers():
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, name, team, source, odoo_employee_id, operation_codes "
            "FROM workers ORDER BY id"
        ).fetchall()
        c.close()
    results = []
    for r in rows:
        try:
            operation_codes = json.loads(r["operation_codes"] or "[]")
        except (TypeError, ValueError):
            operation_codes = []
        if not isinstance(operation_codes, list):
            operation_codes = []
        w = {"id": r["id"], "name": r["name"], "team": r["team"],
             "source": r["source"] if "source" in r.keys() else "local",
             "odooEmployeeId": r["odoo_employee_id"] if "odoo_employee_id" in r.keys() else 0,
             "operationCodes": [str(code) for code in operation_codes]}
        results.append(w)
    return results


def db_add_worker(wid, name, team, source="local", odoo_employee_id=0,
                  operation_codes=None):
    operation_codes = operation_codes or []
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.execute(
            "INSERT INTO workers "
            "(id, name, team, source, odoo_employee_id, operation_codes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (wid, name, team, source, odoo_employee_id,
             json.dumps(operation_codes, ensure_ascii=False))
        )
        c.commit()
        c.close()
    logger.info(f"添加工人: {name} ({wid}), source={source}")


def _normalize_report(row):
    """将 SQLite 字段转为前端格式（兼容 snake_case + camelCase）"""
    base = {
        "id": row["id"], "workerId": row["worker_id"], "workerName": row["worker_name"],
        "workerTeam": row.get("worker_team", ""),
        "orderId": row["order_id"], "orderCustomer": row.get("order_customer", ""),
        "orderProduct": row.get("order_product", ""),
        "operation": row["operation"], "operationLabel": row["operation_label"],
        "qty": row["qty"], "qualified": row["qualified"], "hours": row["hours"],
        "remark": row.get("remark", ""), "date": row["date"], "time": row["time"],
        "timestamp": row["timestamp"],
    }
    # 新字段（同时输出 snake_case 和 camelCase 以兼容前端）
    for f in ["production_id", "workorder_id", "odoo_employee_id", "idempotency_key",
              "odoo_report_id", "odoo_stock_move_ids", "sync_status",
              "material_sync_status", "odoo_progress_qty", "error_message"]:
        if f in row.keys():
            base[f] = row[f]
    # camelCase 别名（前端使用）
    base["productionId"] = row.get("production_id", "")
    base["workorderId"] = row.get("workorder_id", "")
    base["odooEmployeeId"] = row.get("odoo_employee_id", 0)
    base["idempotencyKey"] = row.get("idempotency_key", "")
    base["odooReportId"] = row.get("odoo_report_id", "")
    base["odooStockMoveIds"] = row.get("odoo_stock_move_ids", "")
    base["syncStatus"] = row.get("sync_status", "")
    base["materialSyncStatus"] = row.get("material_sync_status", "unknown")
    base["odooProgressQty"] = row.get("odoo_progress_qty")
    base["errorMessage"] = row.get("error_message", "")
    return base


REPORT_COLS = ["id", "worker_id", "worker_name", "worker_team", "order_id",
               "order_customer", "order_product", "operation", "operation_label",
               "qty", "qualified", "hours", "remark", "date", "time", "timestamp",
               "production_id", "workorder_id", "odoo_employee_id",
               "idempotency_key", "odoo_report_id", "odoo_stock_move_ids",
               "sync_status", "material_sync_status", "odoo_progress_qty",
               "error_message", "created_at"]


def db_reports(date_filter=None, limit=500):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        if date_filter:
            rows = c.execute(
                "SELECT * FROM reports WHERE date = ? ORDER BY timestamp DESC LIMIT ?",
                (date_filter, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM reports ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        c.close()
    return [dict(r) for r in rows]


def db_get_report(rid):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
        c.close()
    return dict(row) if row else None


def db_get_report_by_idempotency(key):
    """根据幂等键查询报工"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM reports WHERE idempotency_key = ? AND idempotency_key != ''",
            (key,)
        ).fetchone()
        c.close()
    return dict(row) if row else None


def db_get_report_by_workorder(worker_id, workorder_id, date, operation):
    """按工人+Odoo工单+日期+工序查询报工，兼容旧记录的 order_id。"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM reports WHERE worker_id=? AND workorder_id=? AND date=? AND operation=?",
            (str(worker_id), str(workorder_id), str(date), str(operation)),
        ).fetchone()
        c.close()
    return dict(row) if row else None


def db_update_report_sync(report_id, sync_status, error_message="",
                          odoo_report_id=None, odoo_stock_move_ids=None,
                          material_sync_status=None, odoo_progress_qty=None):
    """Update Odoo synchronization fields without inserting a second report."""
    assignments = ["sync_status = ?", "error_message = ?"]
    values = [sync_status, error_message or ""]
    optional_values = {
        "odoo_report_id": odoo_report_id,
        "odoo_stock_move_ids": odoo_stock_move_ids,
        "material_sync_status": material_sync_status,
        "odoo_progress_qty": odoo_progress_qty,
    }
    for column, value in optional_values.items():
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(value)
    values.append(report_id)
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        try:
            cursor = c.execute(f"UPDATE reports SET {', '.join(assignments)} WHERE id = ?", values)
            updated = cursor.rowcount == 1
            c.commit()
        finally:
            c.close()
    return updated


def db_add_report(report, materials=None):
    """添加报工，若违反唯一约束则返回 False"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        try:
            c.execute(
                """INSERT INTO reports
                (id, worker_id, worker_name, worker_team, order_id, order_customer,
                 order_product, operation, operation_label, qty, qualified, hours,
                 remark, date, time, timestamp, production_id, workorder_id,
                 odoo_employee_id, idempotency_key, odoo_report_id,
                 odoo_stock_move_ids, sync_status, material_sync_status,
                 odoo_progress_qty, error_message)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report["id"], report["workerId"], report["workerName"],
                 report.get("workerTeam", ""), report["orderId"],
                 report.get("orderCustomer", ""), report.get("orderProduct", ""),
                 report["operation"], report["operationLabel"],
                 report["qty"], report.get("qualified", report["qty"]),
                 report.get("hours", 0), report.get("remark", ""),
                 report["date"], report["time"], report["timestamp"],
                 report.get("productionId", ""), report.get("workorderId", ""),
                 report.get("odooEmployeeId", 0), report.get("idempotencyKey", ""),
                 report.get("odooReportId", ""), report.get("odooStockMoveIds", ""),
                 report.get("syncStatus", "local"),
                 report.get("materialSyncStatus", "unknown"),
                 report.get("odooProgressQty"), report.get("errorMessage", "")),
            )
            # 保存物料记录
            if materials:
                for mat in materials:
                    c.execute(
                        """INSERT INTO report_materials
                        (report_id, product_id, bom_line_id, default_code, actual_qty, uom_id)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (report["id"], mat.get("productId", 0), mat.get("bomLineId", 0),
                         mat.get("defaultCode", ""), mat.get("actualQty", 1), mat.get("uomId", 1)),
                    )
            c.commit()
            ok = True
        except sqlite3.IntegrityError:
            ok = False
        c.close()
    if ok:
        logger.info(f"报工: {report['workerName']} 工单#{report.get('workorderId','')} {report['qty']}台 "
                     f"物料{len(materials) if materials else 0}项 [{report.get('syncStatus','local')}]")
    return ok


def db_get_report_materials(report_id):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM report_materials WHERE report_id = ?", (report_id,)
        ).fetchall()
        c.close()
    return [dict(r) for r in rows]


# ============================================================
# 数据查询函数
# ============================================================

def load_workers():
    return db_workers()


def load_reports():
    return db_reports()


# ============================================================
# 工序定义
# ============================================================

OPERATIONS = [
    {"id": "assembly", "code": "assembly", "name": "总装", "hostType": None,
     "workorderNames": ["组装"]},
    {"id": "testing", "code": "testing", "name": "测试", "hostType": None},
    {"id": "qc", "code": "qc", "name": "质检", "hostType": None},
    {"id": "packing", "code": "packing", "name": "包装", "hostType": None,
     "workorderNames": ["打包"]},
    {"id": "debug", "code": "debug", "name": "调试", "hostType": None},
    {"id": "pc_assembly_tape", "code": "pc_assembly_tape", "name": "电脑装机（编带主机）", "hostType": "tape",
     "odooWorkcenterId": 101, "odooWorkcenterCode": "pc_assembly_tape"},
    {"id": "pc_assembly_splitter", "code": "pc_assembly_splitter", "name": "电脑装机（分光主机）", "hostType": "splitter",
     "odooWorkcenterId": 102, "odooWorkcenterCode": "pc_assembly_splitter"},
    {"id": "test_tape_operation", "code": "test_tape_operation", "name": "测试工序（编带）",
     "hostType": "tape", "odooWorkcenterId": 101,
     "odooWorkcenterCode": "pc_assembly_tape", "mockOnly": True},
    {"id": "test_splitter_operation", "code": "test_splitter_operation", "name": "测试工序（分光）",
     "hostType": "splitter", "odooWorkcenterId": 102,
     "odooWorkcenterCode": "pc_assembly_splitter", "mockOnly": True},
    {"id": "test_assembly_operation", "code": "test_assembly_operation", "name": "测试工序（组装）",
     "hostType": None, "workorderNames": ["组装"], "odooWorkcenterId": 103,
     "odooWorkcenterCode": "test_assembly", "mockOnly": True},
    {"id": "test_packing_operation", "code": "test_packing_operation", "name": "测试工序（打包）",
     "hostType": None, "workorderNames": ["打包"], "odooWorkcenterId": 104,
     "odooWorkcenterCode": "test_packing", "mockOnly": True},
]

VALID_OPERATIONS = {op["code"] for op in OPERATIONS}
OPERATION_MAP = {op["code"]: op for op in OPERATIONS}


WO_STATE_MAP = {
    "draft": "草稿", "pending": "待处理", "ready": "就绪",
    "waiting": "等待中", "progress": "生产中", "done": "完成",
    "cancel": "已取消", "to_close": "待关闭",
}
MO_STATE_MAP = {
    "draft": "草稿", "confirmed": "已确认", "progress": "生产中",
    "done": "完成", "cancel": "已取消", "to_close": "待关闭",
}

# Odoo 库位 ID
SRC_LOCATION_ID = 17      # WH/生产前（工人物料从这个库位扣）
DEST_LOCATION_ID = 15     # Virtual Locations/Production


def get_operations():
    """Return operations that are valid for the active Odoo mode."""
    mode = get_odoo_mode()
    ops = []
    for op in OPERATIONS:
        if mode == "real" and op.get("mockOnly"):
            continue
        o = dict(op)
        o["meta"] = {"mode": mode, "source": "odoo" if mode == "real" else "mock"}
        ops.append(o)
    return ops


# ============================================================
# 认证校验
# ============================================================

# 内网IP白名单（当 API_KEY 未配置时使用）
_ALLOWED_IPS = {"127.0.0.1", "localhost", "::1"}  # 本机
_ALLOWED_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
                     "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")


def check_auth(handler):
    """API Key 或内网 IP 白名单认证"""
    if API_KEY:
        provided = handler.headers.get("X-API-Key", "")
        return provided == API_KEY
    # 无 API_KEY 时：只允许本机和内网
    client_ip = handler.client_address[0]
    if client_ip in _ALLOWED_IPS or any(client_ip.startswith(p) for p in _ALLOWED_PREFIXES):
        return True
    logger.warning(f"拒绝来自 {client_ip} 的 POST 请求 (无 API_KEY 且不在白名单)")
    return False


_worker_ids_lock = threading.Lock()

def get_valid_worker_ids():
    with _worker_ids_lock:
        workers = db_workers()
        return {w["id"] for w in workers}


def get_worker_by_id(worker_id):
    worker_id = str(worker_id or "")
    return next((w for w in db_workers() if str(w.get("id")) == worker_id), None)


def worker_allows_operation(worker_id, operation_code):
    worker = get_worker_by_id(worker_id)
    return bool(worker and operation_code in set(worker.get("operationCodes") or []))


_order_ids_cache = {"ids": set(), "ts": 0, "marker": 0}
_ORDER_CACHE_TTL = 60
_order_ids_lock = threading.Lock()

def get_valid_order_ids():
    now = time.time()
    marker = reset_marker_stamp()
    with _order_ids_lock:
        if (now - _order_ids_cache["ts"] < _ORDER_CACHE_TTL
                and _order_ids_cache["marker"] == marker
                and _order_ids_cache["ids"]):
            return _order_ids_cache["ids"]
    try:
        data = load_dashboard()
        ids = {row["order"] for row in data.get("deliveryRows", [])}
        with _order_ids_lock:
            _order_ids_cache["ids"] = ids
            _order_ids_cache["ts"] = time.time()
            _order_ids_cache["marker"] = reset_marker_stamp()
        return ids
    except Exception:
        with _order_ids_lock:
            return _order_ids_cache["ids"]


# ============================================================
# Odoo 辅助函数
# ============================================================

def rel_name(value, fallback=""):
    if isinstance(value, list) and len(value) > 1:
        return str(value[1])
    if value in (False, None, ""):
        return fallback
    return str(value)


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return None


def clean_name(value):
    return re.sub(r"^\[[^\]]+\]\s*", "", rel_name(value)).strip()


def product_code(display, default=""):
    text = rel_name(display)
    match = re.match(r"^\[([^\]]+)\]", text)
    return match.group(1) if match else default


def workorder_host_type(product, workcenter=None):
    """Infer the host type from product/workcenter data.

    Product codes remain the strongest signal, while names provide a stable
    fallback when a new product code is introduced in Odoo.
    """
    code = product_code(product).upper()
    if code == "P04725":
        return "tape"
    if code == "P04726":
        return "splitter"
    searchable = f"{rel_name(product)} {rel_name(workcenter)}".casefold()
    if any(token in searchable for token in ("编带", "tape")):
        return "tape"
    if any(token in searchable for token in ("分光", "splitter")):
        return "splitter"
    return None


def operation_matches_workorder(operation, workorder):
    """Return whether an operation binding can use a specific Odoo WO."""
    if not operation or not workorder:
        return False
    expected_host = operation.get("hostType")
    if expected_host and workorder.get("hostType") != expected_host:
        return False
    names = operation.get("workorderNames") or []
    if names and workorder.get("workorderName") not in names:
        return False
    return True


def bracket_code(value):
    match = re.match(r"^\[([^\]]+)\]", value or "")
    return match.group(1) if match else value or "-"


def number(value):
    return float(value or 0)


def qty_text(value):
    value = number(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def local_time(value, fmt="%m-%d %H:%M"):
    dt = parse_dt(value)
    return dt.astimezone(LOCAL_TZ).strftime(fmt) if dt else "-"


def local_dt(value, fmt="%m-%d %H:%M"):
    return value.astimezone(LOCAL_TZ).strftime(fmt) if value else "-"


def max_dt(*values):
    dates = [dt for dt in (parse_dt(value) for value in values) if dt]
    return max(dates) if dates else None


def order_number(value):
    match = re.search(r"(\d+)$", value or "")
    return int(match.group(1)) if match else 0


def due_state(value, remaining):
    if remaining <= 0:
        return "已完成"
    dt = parse_dt(value)
    if not dt:
        return "待交付"
    today = datetime.now(LOCAL_TZ).date()
    day = dt.astimezone(LOCAL_TZ).date()
    if day < today:
        return "已逾期"
    if day == today:
        return "今日交付"
    return "待交付"


def build_stages(qty, delivered, remaining, need_qty, supplier, mrp, delivery_status):
    mrp_state = (mrp or {}).get("state") or ""
    mrp_labels = {
        "draft": "草稿", "confirmed": "待生产", "progress": "生产中",
        "to_close": "待关闭", "done": "完成", "cancel": "取消",
    }
    if remaining <= 0:
        return [
            ["销售订单", "完成", "done"],
            ["库存预警", "完成", "done"],
            ["采购下单", "完成", "done"],
            ["生产规划", "完成", "done"],
            ["交付", "完成", "done"],
        ]
    stock_stage = ["库存预警", f"缺{qty_text(need_qty)}", "warning"] if need_qty > 0 else ["库存预警", "库存OK", "done"]
    if need_qty > 0:
        purchase_stage = ["采购下单", "待下单", "running"] if supplier else ["采购下单", "待配供应商", "warning"]
    else:
        purchase_stage = ["采购下单", "无需采购", "done"]
    if mrp:
        production_stage = [
            "生产规划", mrp_labels.get(mrp_state, mrp_state or "已录入"),
            "done" if mrp_state in ("done", "to_close") else "running" if mrp_state == "progress" else "warning",
        ]
    elif need_qty > 0:
        production_stage = ["生产规划", "待规划", "pending"]
    else:
        production_stage = ["生产规划", "待录入", "pending"]
    if delivered > 0:
        delivery_stage = ["交付", f"已交{qty_text(delivered)}", "running"]
    elif delivery_status == "已逾期":
        delivery_stage = ["交付", "已逾期", "danger"]
    else:
        delivery_stage = ["交付", "待交付", "pending"]
    return [
        ["销售订单", "完成", "done"],
        stock_stage, purchase_stage, production_stage, delivery_stage,
    ]


# ============================================================
# Dashboard 数据加载
# ============================================================
_DASH_CACHE = {"data": None, "ts": 0, "marker": 0}
_DASH_CACHE_LOCK = threading.Lock()
_DASH_CACHE_TTL = 120  # 2分钟缓存，准实时


def load_dashboard():
    """加载 Dashboard 数据（120 秒缓存，减少 Odoo 重复查询）。"""
    now = time.time()
    marker = reset_marker_stamp()
    with _DASH_CACHE_LOCK:
        if (_DASH_CACHE["data"] is not None
                and _DASH_CACHE["marker"] == marker
                and (now - _DASH_CACHE["ts"]) < _DASH_CACHE_TTL):
            logger.info("Dashboard缓存命中")
            return _DASH_CACHE["data"]

    client = get_odoo()
    recent_start = (datetime.now(LOCAL_TZ) - timedelta(days=7)).astimezone(timezone.utc)
    recent_start_text = recent_start.strftime("%Y-%m-%d %H:%M:%S")

    op_fields = [
        "name", "product_id", "spec_info", "qty_on_hand", "qty_forecast",
        "qty_to_order", "product_uom_name", "product_supplier_id", "write_date",
    ]
    try:
        orderpoint_rows = client.search_read(
            "stock.warehouse.orderpoint", [["qty_to_order", ">", 0]],
            op_fields, limit=80, order="write_date desc",
        )
    except Exception:
        orderpoint_rows = []
    ops_by_product_id = {rel_id(row.get("product_id")): row for row in orderpoint_rows}
    ops_by_code = {product_code(row.get("product_id")): row for row in orderpoint_rows}

    order_fields = [
        "name", "partner_id", "user_id", "state", "date_order",
        "expected_date", "commitment_date", "delivery_status", "amount_total", "write_date",
    ]
    line_fields = [
        "order_id", "product_id", "default_code", "spec_info", "name",
        "product_uom_qty", "qty_delivered", "qty_to_deliver", "product_uom",
        "state", "scheduled_date", "create_date", "write_date",
    ]

    try:
        recent_orders = client.search_read(
            "sale.order", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
            order_fields, limit=160, order="write_date desc",
        )
    except Exception:
        recent_orders = []
    recent_order_ids = [row["id"] for row in recent_orders]
    try:
        recent_line_rows = client.search_read(
            "sale.order.line", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
            line_fields, limit=200, order="write_date desc",
        )
    except Exception:
        recent_line_rows = []
    linked_line_rows = []
    if recent_order_ids:
        try:
            linked_line_rows = client.search_read(
                "sale.order.line", [["state", "=", "sale"], ["order_id", "in", recent_order_ids]],
                line_fields, limit=200, order="write_date desc",
            )
        except Exception:
            linked_line_rows = []
    sale_line_map = {row["id"]: row for row in recent_line_rows}
    sale_line_map.update({row["id"]: row for row in linked_line_rows})
    sale_lines = list(sale_line_map.values())
    order_ids = sorted({rel_id(line.get("order_id")) for line in sale_lines if rel_id(line.get("order_id"))})
    orders = {row["id"]: row for row in recent_orders}
    missing_order_ids = [oid for oid in order_ids if oid not in orders]
    try:
        orders.update({row["id"]: row for row in client.read("sale.order", missing_order_ids, order_fields)})
    except Exception:
        pass

    mrp_rows = []
    try:
        mrp_rows = client.search_read(
            "mrp.production", [["state", "not in", ["done", "cancel"]]],
            ["name", "origin", "product_id", "product_qty", "qty_produced",
             "state", "reservation_state", "date_deadline", "write_date"],
            limit=120, order="write_date desc",
        )
    except Exception:
        mrp_rows = []
    mrp_by_product_id = {rel_id(row.get("product_id")): row for row in mrp_rows}
    mrp_by_code = {product_code(row.get("product_id")): row for row in mrp_rows}

    delivery_rows = []
    total_qty = delivered_qty = remaining_qty = 0.0
    for line in sale_lines:
        qty = number(line.get("product_uom_qty"))
        delivered = min(max(number(line.get("qty_delivered")), 0), qty)
        remaining = min(max(number(line.get("qty_to_deliver")), qty - delivered, 0), qty)
        if remaining <= 0:
            continue
        order = orders.get(rel_id(line.get("order_id")), {})
        code = line.get("default_code") or product_code(line.get("product_id"))
        op = ops_by_product_id.get(rel_id(line.get("product_id"))) or ops_by_code.get(code) or {}
        need_qty = number(op.get("qty_to_order"))
        product = clean_name(line.get("product_id"))
        spec = line.get("spec_info") or op.get("spec_info") or "-"
        due = order.get("commitment_date")
        display_due = due or order.get("date_order")
        status = due_state(due, remaining)
        mrp = mrp_by_product_id.get(rel_id(line.get("product_id"))) or mrp_by_code.get(code)
        if remaining > 0 and need_qty > 0:
            status = "待采购" if not op.get("product_supplier_id") else "待下单"
        elif remaining > 0 and mrp:
            status = "生产中" if mrp.get("state") == "progress" else "已规划"
        remark = "补货缺口 " + qty_text(need_qty) if need_qty > 0 else ("已交付" if remaining <= 0 else "Odoo待交付")
        updated_at = max_dt(
            line.get("write_date"), order.get("write_date"),
            op.get("write_date"), (mrp or {}).get("write_date"),
        )
        total_qty += qty
        delivered_qty += delivered
        remaining_qty += remaining
        delivery_rows.append({
            "customer": rel_name(order.get("partner_id"), "-"),
            "customerCode": bracket_code(rel_name(order.get("partner_id"), "-")),
            "order": rel_name(line.get("order_id"), "-"),
            "machine": product, "code": code, "spec": spec,
            "qty": qty_text(qty), "uom": rel_name(line.get("product_uom"), ""),
            "remark": remark, "splitter": "-", "delivery": status,
            "owner": rel_name(order.get("user_id"), "-"),
            "date": local_time(display_due), "updated": local_dt(updated_at),
            "remaining": qty_text(remaining),
            "priority": "danger" if status == "已逾期" else "warning" if status in ("待采购", "待下单", "已规划") else "running",
            "stages": build_stages(qty, delivered, remaining, need_qty, op.get("product_supplier_id"), mrp, status),
            "_sort": order_number(rel_name(line.get("order_id"), "")),
            "_updated_ts": updated_at.timestamp() if updated_at else 0,
        })
    delivery_rows.sort(key=lambda row: (row.get("_updated_ts", 0), row.get("_sort", 0)), reverse=True)

    replenish_rows = []
    recent_orderpoint_rows = [r for r in orderpoint_rows if (parse_dt(r.get("write_date")) or datetime.min.replace(tzinfo=timezone.utc)) >= recent_start]
    for row in recent_orderpoint_rows:
        replenish_rows.append({
            "product": clean_name(row.get("product_id")),
            "code": product_code(row.get("product_id")),
            "spec": row.get("spec_info") or "-",
            "onHand": qty_text(row.get("qty_on_hand")),
            "forecast": qty_text(row.get("qty_forecast")),
            "toOrder": qty_text(row.get("qty_to_order")),
            "uom": row.get("product_uom_name") or "",
            "supplier": rel_name(row.get("product_supplier_id"), "待配供应商"),
            "updated": local_time(row.get("write_date")),
        })
    pending_rows = delivery_rows
    pending_order_count = len({row["order"] for row in pending_rows})
    replenish_qty = sum(number(r.get("qty_to_order")) for r in recent_orderpoint_rows)
    supplier_missing = sum(1 for r in recent_orderpoint_rows if number(r.get("qty_to_order")) > 0 and not r.get("product_supplier_id"))
    active_mrp_count = 0
    try:
        active_mrp_count = client.call("mrp.production", "search_count", [[["state", "not in", ["done", "cancel"]]]], {})
    except Exception:
        active_mrp_count = 0

    mode = get_odoo_mode()
    kpis = [
        ["最近待处理", str(pending_order_count), "单", "最近7天更新", "#3b82f6"],
        ["待处理行", str(len(pending_rows)), "行", "交付后自动消失", "#22b8cf"],
        ["待交付数量", qty_text(remaining_qty), "台/套", "qty_to_deliver 汇总", "#20b26b"],
        ["补货缺口", qty_text(replenish_qty), "台", f"近7天补货 {len(recent_orderpoint_rows)} 条", "#f07a35"],
        ["待配供应商", str(supplier_missing), "条", "补货规则未配置供应商", "#eab842"],
        ["数据来源", "Odoo" if mode == "real" else "模拟", "", f"模式: {mode}", "#eab842"],
    ]
    alerts = []
    for row in delivery_rows[:3]:
        alerts.append([row["delivery"], row["order"],
                       f"{row['customer']} · {row['machine']} {row['spec']} · 数量 {row['qty']}，{row['remark']}，更新 {row['updated']}",
                       row["owner"]])
    if not alerts:
        alerts.append(["提示", "暂无待处理", "最近7天暂无需要展示的在产订单。", "-"])
    latest_orders = []
    seen = set()
    for row in delivery_rows:
        if row["order"] in seen:
            continue
        seen.add(row["order"])
        latest_orders.append([row["order"], row["customer"], row["updated"], row["delivery"]])
        if len(latest_orders) >= 6:
            break
    result = {
        "kpis": kpis, "deliveryRows": delivery_rows[:12],
        "replenishments": replenish_rows[:6], "latestOrders": latest_orders,
        "alerts": alerts,
        "meta": {
            "source": "odoo" if mode == "real" else "mock",
            "mode": mode,
            "db": ODOO_DB, "user": ODOO_USER,
            "updatedAt": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "accuracyNote": "客户、订单、产品、规格、数量、待交付、补货缺口、供应商配置均来自 Odoo 原字段。",
            "range": "最近7天更新",
            "progressNote": "核心区只展示最近7天更新且仍待交付的订单行。",
        },
    }
    # Do not let a request that started before reset repopulate the cache with
    # stale Odoo data after the reset marker changes.
    with _DASH_CACHE_LOCK:
        if reset_marker_stamp() == marker:
            _DASH_CACHE["data"] = result
            _DASH_CACHE["ts"] = time.time()
            _DASH_CACHE["marker"] = marker
    return result


# ============================================================
# 新增: BOM 数据查询
# ============================================================

# BOM 定义（基于真实 Odoo 调查 + Excel）
TAPE_BOM_CODES = ["P04725", "P05346", "P05347", "P05350", "P05351", "P05352", "P05353"]
SPLITTER_BOM_CODES = ["P04726", "P05346", "P05347", "P05348", "P05351", "P05352", "P05353"]

# 真实 Odoo product ID 映射
ODOO_PRODUCT_IDS = {
    "P04725": 11632, "P05346": 12253, "P05347": 12254, "P05350": 12257,
    "P05348": 12255, "P05351": 12258, "P05352": 12259, "P05353": 12260,
    "P04726": 11633,
}

# 真实 Odoo product.template ID 映射
ODOO_TMPL_IDS = {
    "P04725": 12977, "P05346": 13001, "P05347": 13002, "P05350": 13005,
    "P05348": 13003, "P05351": 13006, "P05352": 13007, "P05353": 13008,
    "P04726": 12978,
}

# BOM Line ID 映射（Mock 模式下使用）
MOCK_BOM_LINE_IDS = {
    "tape": {"P04725": 3001, "P05346": 3002, "P05347": 3003, "P05350": 3004,
             "P05351": 3005, "P05352": 3006, "P05353": 3007},
    "splitter": {"P04726": 3008, "P05346": 3009, "P05347": 3010, "P05348": 3011,
                 "P05351": 3012, "P05352": 3013, "P05353": 3014},
}

# Excel BOM 数据（来自主机BOM物料清单登记表.xlsx）
EXCEL_BOM = {
    "tape": [
        {"seq": 1, "defaultCode": "P04725", "name": "编带机箱", "spec": "黑色:4U300",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 2, "defaultCode": "P05346", "name": "cpu", "spec": "I3-3220",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 3, "defaultCode": "P05347", "name": "内存条", "spec": "DDR3-4G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 4, "defaultCode": "P05350", "name": "硬盘", "spec": "SSD-128G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 5, "defaultCode": "P05351", "name": "显卡", "spec": "G210",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 6, "defaultCode": "P05352", "name": "机箱电源", "spec": "ATX-400W",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 7, "defaultCode": "P05353", "name": "机箱风扇", "spec": "",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
    ],
    "splitter": [
        {"seq": 1, "defaultCode": "P04726", "name": "分光机箱", "spec": "4U-610H",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 2, "defaultCode": "P05346", "name": "cpu", "spec": "I3-3220",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 3, "defaultCode": "P05347", "name": "内存条", "spec": "DDR3-4G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 4, "defaultCode": "P05348", "name": "硬盘", "spec": "SSD-64G",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 5, "defaultCode": "P05351", "name": "显卡", "spec": "G210",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 6, "defaultCode": "P05352", "name": "机箱电源", "spec": "ATX-400W",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
        {"seq": 7, "defaultCode": "P05353", "name": "机箱风���", "spec": "",
         "uom": "pcs", "qty": 1, "category": "主机配件", "brand": "淘宝"},
    ],
}


# ============================================================
# BOM 数据缓存（线程安全）
# ============================================================
_BOM_CACHE = {"data": None, "ts": 0, "key": None, "marker": 0}
_BOM_CACHE_LOCK = threading.Lock()
_BOM_CACHE_TTL = 30      # 30秒缓存，确保 Odoo BOM 修改后能快速生效


def get_bom_data(host_type):
    """获取 BOM 数据（缓存 + 线程安全）"""
    if host_type not in ("tape", "splitter"):
        return []

    now = time.time()
    marker = reset_marker_stamp()
    cache_key = f"{get_odoo_mode()}:{host_type}"
    with _BOM_CACHE_LOCK:
        if (_BOM_CACHE["key"] == cache_key
                and _BOM_CACHE["data"] is not None
                and _BOM_CACHE["marker"] == marker
                and (now - _BOM_CACHE["ts"]) < _BOM_CACHE_TTL):
            logger.info(f"BOM缓存命中 [{host_type}]")
            return _BOM_CACHE["data"]

    codes = TAPE_BOM_CODES if host_type == "tape" else SPLITTER_BOM_CODES
    excel_items = EXCEL_BOM.get(host_type, [])
    bom_line_ids = MOCK_BOM_LINE_IDS.get(host_type, {})
    mode = get_odoo_mode()

    client = get_odoo() if mode == "real" else None

    # 真模式：直接从 Odoo mrp.bom 拉最新的 BOM lines（按成品 product code 找）
    # 编带机箱 → 找 product.code=P04725 的 BOM；分光机箱 → P04726
    bom_items_data = []  # [{product_code, qty, sequence}]
    if mode == "real" and client:
        try:
            target_product_code = "P04725" if host_type == "tape" else "P04726"
            # 找到对应的 product.template
            tmpl = models_query_tmpl_by_code(client, target_product_code)
            if tmpl:
                # 找这个产品的最新 BOM（按 code/name 排序，取第一条）
                bom_ids = client.call("mrp.bom", "search", [
                    [("product_tmpl_id", "=", tmpl), ("type", "=", "normal")]
                ], {"order": "id desc", "limit": 1})
                if bom_ids:
                    lines = client.call("mrp.bom.line", "search_read",
                        [[("bom_id", "=", bom_ids[0])]],
                        {"fields": ["id", "product_id", "product_qty", "sequence"]})
                    for ln in lines:
                        pid = rel_id(ln.get("product_id"))
                        bom_items_data.append({
                            "bom_line_id": ln["id"],
                            "product_id": pid,
                            "product_qty": float(ln.get("product_qty", 1)),
                            "sequence": ln.get("sequence", 0),
                        })
                    logger.info(f"BOM[{bom_ids[0]}] 从 Odoo 拉到 {len(bom_items_data)} 条 lines")
                else:
                    logger.warning(f"Odoo 中找不到 product={target_product_code} 的 BOM")
            else:
                logger.warning(f"Odoo 中找不到 product.code={target_product_code}")
        except Exception as e:
            logger.warning(f"查询 mrp.bom 失败: {e}")

    # 降级：Odoo 查不到时回退到 EXCEL_BOM 硬编码
    if not bom_items_data:
        for i, code in enumerate(codes):
            excel = excel_items[i] if i < len(excel_items) else {}
            pid = 0
            for _pid, pdata in ({} if not mode == "real" or not client else
                                  {pid: pdata for pid, pdata in {}}.items()):
                pass
            bom_items_data.append({
                "bom_line_id": bom_line_ids.get(code, 0),
                "product_id": 0,
                "product_code": code,
                "product_qty": excel.get("qty", 1),
                "sequence": i + 1,
            })
        logger.info(f"降级使用 EXCEL_BOM 硬编码 {len(bom_items_data)} 条")

    # 一次性查出所有 product.product
    products_by_id = {}
    if mode == "real" and client:
        # 从 bom_items_data 提取所有 product_id
        all_pids = [d["product_id"] for d in bom_items_data if d.get("product_id")]
        if all_pids:
            try:
                rows = client.search_read(
                    "product.product",
                    [("id", "in", list(set(all_pids)))],
                    ["id", "default_code", "name", "product_tmpl_id", "categ_id", "uom_id", "seller_ids"],
                    limit=50
                )
                for r in rows:
                    tmpl = r.get("product_tmpl_id")
                    tmpl_id = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
                    products_by_id[r["id"]] = {
                        "code": r.get("default_code", ""),
                        "name": r.get("name", ""),
                        "categ": r.get("categ_id", ""),
                        "uom": r.get("uom_id", ""),
                        "tmpl_id": tmpl_id,
                        "seller_ids": r.get("seller_ids", []) or [],
                    }
            except Exception as e:
                logger.warning(f"获取 product.product 失败: {e}")

    # 一次性查出所有 product.template（仅用于规格）
    templates_by_id = {}
    tmpl_ids = {p["tmpl_id"] for p in products_by_id.values() if p.get("tmpl_id")}
    if mode == "real" and client and tmpl_ids:
        try:
            trows = client.read(
                "product.template",
                list(tmpl_ids),
                ["id", "spec_info"]
            )
            for tr in trows:
                templates_by_id[tr["id"]] = {
                    "spec": tr.get("spec_info", "") or "",
                }
        except Exception as e:
            logger.warning(f"获取 product.template 失败: {e}")

    # 一次性查出所有供应商（product.supplierinfo），通过 partner_id 取供应商名称
    supplier_name_by_pid = {}  # {product.product.id: 供应商名称}
    all_seller_ids = []
    for pdata in products_by_id.values():
        for sid in pdata.get("seller_ids", []):
            if sid not in all_seller_ids:
                all_seller_ids.append(sid)
    if mode == "real" and client and all_seller_ids:
        try:
            srows = client.read(
                "product.supplierinfo",
                all_seller_ids,
                ["id", "product_id", "partner_id"]
            )
            # 建立 supplierinfo_id -> partner_name 映射
            sinfo_to_partner = {}
            for sr in srows:
                p = sr.get("partner_id", "")
                if isinstance(p, (list, tuple)) and len(p) > 1:
                    sinfo_to_partner[sr["id"]] = p[1]
            # 反向映射：product.product.id -> 供应商名称
            for pdata in products_by_id.values():
                for sid in pdata.get("seller_ids", []):
                    if sid in sinfo_to_partner and pdata["code"]:
                        supplier_name_by_pid[pdata["code"]] = sinfo_to_partner[sid]
                        break
        except Exception as e:
            logger.warning(f"获取 product.supplierinfo 失败: {e}")

    # 一次性批量查询所有物料的库存
    stock_by_pid = {}  # {product_id: available_quantity}
    if mode == "real" and client and products_by_id:
        try:
            product_ids = list(products_by_id.keys())
            stock_rows = client.search_read(
                "stock.quant",
                [("product_id", "in", product_ids), ("quantity", ">", 0)],
                ["product_id", "available_quantity"], 50
            )
            for s in stock_rows:
                pid = rel_id(s.get("product_id"))
                avail = number(s.get("available_quantity", 0))
                stock_by_pid[pid] = stock_by_pid.get(pid, 0) + avail
        except Exception as e:
            logger.warning(f"批量查询 stock.quant 失败: {e}")

    items = []
    # 按 bom_items_data 顺序构建 items（每个 line 对应一个物料）
    # code -> excel 数据映射（兼容降级场景）
    code_to_excel = {}
    for i, code in enumerate(codes):
        if i < len(excel_items):
            code_to_excel[code] = excel_items[i]

    for bom_item in bom_items_data:
        pid = bom_item.get("product_id", 0)
        bom_line_id = bom_item.get("bom_line_id", 0)
        odoo_qty = bom_item.get("product_qty", 1)

        # 取这个 product 的代码
        pdata = products_by_id.get(pid, {}) if pid else {}
        code = bom_item.get("product_code") or pdata.get("code", "")
        excel = code_to_excel.get(code, {})

        # 默认值（Odoo 读不到时回退到 Excel）
        product_name = pdata.get("name", "") or excel.get("name", "")
        spec = ""
        tmpl_id = pdata.get("tmpl_id", 0) or 0
        if tmpl_id and tmpl_id in templates_by_id:
            spec = templates_by_id[tmpl_id].get("spec", "") or excel.get("spec", "")
        else:
            spec = excel.get("spec", "")
        category_name = excel.get("category", "主机配件")
        brand_name = supplier_name_by_pid.get(code, excel.get("brand", ""))

        # 清理供应商名称
        if brand_name and brand_name.startswith("["):
            m = re.match(r"^\[[^\]]+\]\s*(.+)$", brand_name)
            if m:
                brand_full = m.group(1).strip()
                if "淘宝" in brand_full:
                    brand_name = "淘宝"
                else:
                    brand_name = brand_full

        available_qty = stock_by_pid.get(pid, 0) if pid else 0

        item = {
            "bomLineId": bom_line_id,
            "productId": pid,
            "productTemplateId": tmpl_id,
            "defaultCode": code,
            "name": product_name,
            "specification": spec,
            "uomId": 1,
            "uomName": "pcs",
            "bomQty": odoo_qty,
            "categoryName": category_name,
            "brandSupplierName": brand_name,
            "availableQty": available_qty,
            "selected": True,
            "actualQty": odoo_qty,
            "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
        }
        items.append(item)

    # 写入缓存（线程安全）
    with _BOM_CACHE_LOCK:
        if reset_marker_stamp() == marker:
            _BOM_CACHE["data"] = items
            _BOM_CACHE["ts"] = time.time()
            _BOM_CACHE["key"] = cache_key
            _BOM_CACHE["marker"] = marker
    return items


def models_query_tmpl_by_code(client, default_code):
    """通过 default_code 查 product.template 的 id"""
    try:
        rows = client.search_read("product.product", [("default_code", "=", default_code)],
                                   ["product_tmpl_id"], limit=1)
        if rows:
            tmpl = rows[0].get("product_tmpl_id")
            return tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
    except Exception as e:
        logger.warning(f"models_query_tmpl_by_code 失败: {e}")
    return None


_WO_CACHE = {"data": None, "ts": 0, "marker": 0}
_WO_CACHE_LOCK = threading.Lock()
_WO_CACHE_TTL = 60  # 60秒缓存，准实时


def get_workorders_data():
    """
    获取活跃工单列表（60秒缓存，准实时）
    过滤条件：
      - 所属 MO 未完成（state 非 done/cancel）
      - MO 在 30 天内有过任何更新（write_date >= 30 天前）
      - 工单有 operation_id + workcenter_id
      - 工序 PDF 优先但非必须
    """
    now = time.time()
    marker = reset_marker_stamp()
    with _WO_CACHE_LOCK:
        if (_WO_CACHE["data"] is not None
                and _WO_CACHE["marker"] == marker
                and (now - _WO_CACHE["ts"]) < _WO_CACHE_TTL):
            return _WO_CACHE["data"]
    mode = get_odoo_mode()
    try:
        client = get_odoo()

        # 日期下限：30 天前的 00:00 UTC（覆盖正常生产周期）
        lookback_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
        lookback_start_text = lookback_start.strftime("%Y-%m-%d %H:%M:%S")

        # 第一步：找未完成的 MO（不按日期过滤，方便实时同步新 MO）
        mo_fields = ["id", "name", "bom_id", "product_id", "product_qty", "origin", "state", "write_date"]
        mo_rows = client.search_read(
            "mrp.production",
            [("write_date", ">=", lookback_start_text),
             ("state", "not in", ["done", "cancel"])],
            mo_fields, limit=200, order="write_date desc"
        )
        # 过滤：只保留 bom 有 routing 的 MO
        bom_ids = set()
        for mo in mo_rows:
            bom_id = rel_id(mo.get("bom_id"))
            if bom_id:
                bom_ids.add(bom_id)
        boms_with_routing = set()
        if bom_ids:
            bom_data = client.read("mrp.bom", list(bom_ids), ["id", "operation_ids"])
            for b in bom_data:
                if b.get("operation_ids"):
                    boms_with_routing.add(b["id"])
        valid_mo_ids = {mo["id"] for mo in mo_rows if rel_id(mo.get("bom_id")) in boms_with_routing}
        logger.info(f"今日 MO 总数: {len(mo_rows)}, 含routing: {len(valid_mo_ids)}")

        # 第二步：找出这些 MO 的工单，且必须有 operation_id 和 workcenter_id
        wo_fields = ["id", "name", "production_id", "workcenter_id", "operation_id",
                     "product_id", "state", "qty_production", "qty_produced",
                     "qty_remaining", "duration_expected", "write_date"]
        if not valid_mo_ids:
            with _WO_CACHE_LOCK:
                if reset_marker_stamp() == marker:
                    _WO_CACHE["data"] = []
                    _WO_CACHE["ts"] = time.time()
                    _WO_CACHE["marker"] = marker
            return []
        wo_rows = client.search_read(
            "mrp.workorder",
            [("production_id", "in", list(valid_mo_ids)),
             ("operation_id", "!=", False),
             ("workcenter_id", "!=", False),
             ("state", "not in", ["done", "cancel"])],
            wo_fields, limit=50, order="id desc"
        )
        logger.info(f"工单候选: {len(wo_rows)}条")

        # 第三步：检查工单对应的 routing.workcenter 是否有 PDF
        # 优先显示有 PDF 的，没有 PDF 的也保留（运维可能还未上传）
        # 兼容 Odoo：部分实例可能没有 mrp.routing.workcenter 模型，包裹 try/except
        ops_with_pdf = set()
        try:
            op_ids = {rel_id(wo.get("operation_id")) for wo in wo_rows if rel_id(wo.get("operation_id"))}
            if op_ids:
                op_data = client.read("mrp.routing.workcenter", list(op_ids),
                                      ["id", "worksheet", "worksheet_type"])
                for op in op_data:
                    if op.get("worksheet") or op.get("worksheet_type") == "pdf":
                        ops_with_pdf.add(op["id"])
        except Exception as routing_err:
            # 用户 Odoo 中可能没有 routing.workcenter 模型，继续执行，不阻断工单展示
            logger.debug(f"routing.workcenter 读取跳过（兼容模式）: {routing_err}")
        # 分类：有 PDF 的优先在前，没有的排在后面
        wo_with_pdf = [wo for wo in wo_rows if rel_id(wo.get("operation_id")) in ops_with_pdf]
        wo_without_pdf = [wo for wo in wo_rows if rel_id(wo.get("operation_id")) not in ops_with_pdf]
        wo_rows = wo_with_pdf + wo_without_pdf  # 有PDF的优先
        if wo_without_pdf:
            logger.info(f"工单含PDF: {len(wo_with_pdf)}条, 暂缺PDF: {len(wo_without_pdf)}条（仍显示）")

        # 获取生产单信息
        mo_ids = set()
        for wo in wo_rows:
            pid = rel_id(wo.get("production_id"))
            if pid:
                mo_ids.add(pid)

        mo_data = {}
        if mo_ids:
            mo_rows = client.read("mrp.production", list(mo_ids),
                                  ["id", "name", "product_id", "product_qty", "state", "origin"])
            for mo in mo_rows:
                mo_data[mo["id"]] = mo

        workorders = []
        # 状态翻译
        for wo in wo_rows:
            mo_id = rel_id(wo.get("production_id"))
            mo = mo_data.get(mo_id, {})
            pid = rel_id(wo.get("product_id"))
            pcode = product_code(wo.get("product_id"))  # 传入 tuple，不要传 pid(int)

            # 确定主机类型（用产品编码，不依赖固定 ID）
            host_type = workorder_host_type(
                wo.get("product_id"), wo.get("workcenter_id")
            )
            OdooRemainingQuantityFix.apply_to_workorder_fix(wo)

            raw_state = wo.get("state", "")
            state_cn = WO_STATE_MAP.get(raw_state, raw_state)
            # 净化产品名（去掉 [编码] 前缀）
            raw_product = rel_name(wo.get("product_id"), "")
            product_name = re.sub(r"^\[[^\]]+\]\s*", "", raw_product).strip()

            workorders.append({
                "workorderId": wo["id"],
                "workorderName": wo.get("name", ""),
                "productionId": mo_id,
                "productionName": mo.get("name", ""),
                "productId": pid,
                "productCode": pcode,
                "productName": product_name or raw_product,
                "workcenterId": rel_id(wo.get("workcenter_id")),
                "workcenterName": rel_name(wo.get("workcenter_id"), ""),
                "operationId": rel_id(wo.get("operation_id")),
                "state": raw_state,
                "stateLabel": state_cn,
                "qtyProduction": number(wo.get("qty_production")),
                "qtyProduced": number(wo.get("qty_produced")),
                "remainingQty": number(wo.get("qty_remaining")),
                "hostType": host_type,
            })
        with _WO_CACHE_LOCK:
            if reset_marker_stamp() == marker:
                _WO_CACHE["data"] = workorders
                _WO_CACHE["ts"] = time.time()
                _WO_CACHE["marker"] = marker
        return workorders
    except Exception as e:
        logger.warning(f"获取工单失败: {e}")
        raise


def _invalidate_runtime_caches():
    """Clear each runtime cache under the lock used by its readers."""
    with _BOM_CACHE_LOCK:
        _BOM_CACHE["data"] = None
        _BOM_CACHE["ts"] = 0
        _BOM_CACHE["key"] = None
        _BOM_CACHE["marker"] = 0
    with _DASH_CACHE_LOCK:
        _DASH_CACHE["data"] = None
        _DASH_CACHE["ts"] = 0
        _DASH_CACHE["marker"] = 0
    with _WO_CACHE_LOCK:
        _WO_CACHE["data"] = None
        _WO_CACHE["ts"] = 0
        _WO_CACHE["marker"] = 0


# ============================================================
# ESOP — 电子作业指导书模块
# ============================================================

def get_sop_for_workorder(workorder_id: int) -> list:
    """
    查询工单的 SOP 装配指导书
    用户 Odoo 18 用 mrp.workorder.worksheet（binary）字段存储 SOP PDF。
    返回附件列表 [{id, name, fileType, version, sopUrl}]，二进制走 download 接口按需取。
    """
    client = get_odoo()
    fields = ["id", "name", "worksheet", "picture", "write_date"]
    recs = client.read("mrp.workorder", [workorder_id], fields)
    if not recs:
        return []
    w = recs[0]
    result = []
    # worksheet 字段存 PDF/装配图（base64）
    if w.get("worksheet"):
        result.append({
            "id": w["id"] * 1000,            # 虚拟 id 区分类型
            "name": (w.get("name") or "作业指导书") + "-SOP",
            "fileType": "application/pdf",
            "version": w.get("write_date", ""),
            "sopUrl": f"/api/sop/download?workorderId={workorder_id}&type=worksheet",
            "workorderId": workorder_id,
            "kind": "worksheet",
        })
    # picture 字段是图片（base64）
    if w.get("picture"):
        result.append({
            "id": w["id"] * 1000 + 1,
            "name": (w.get("name") or "作业指导书") + "-图示",
            "fileType": "image/png",
            "version": w.get("write_date", ""),
            "sopUrl": f"/api/sop/download?workorderId={workorder_id}&type=picture",
            "workorderId": workorder_id,
            "kind": "picture",
        })
    return result


def get_sop_download(workorder_id: int, kind: str) -> tuple | None:
    """从 mrp.workorder 读取 worksheet/picture 字段的二进制数据"""
    client = get_odoo()
    field_name = "worksheet" if kind == "worksheet" else "picture"
    default_mime = "application/pdf" if kind == "worksheet" else "image/png"
    try:
        recs = client.read("mrp.workorder", [workorder_id], [field_name, "name"])
        if recs:
            data = recs[0].get(field_name)
            if data:
                filename = (recs[0].get("name") or "SOP") + ("-SOP.pdf" if kind == "worksheet" else "-图示.png")
                return (default_mime, data, filename)
    except Exception as e:
        logger.warning(f"SOP download error: {e}")
    return None


def log_sop_view(attachment_id, worker_id, worker_name, workorder_id):
    """记录 SOP 查阅日志"""
    try:
        with DB_LOCK:
            c = sqlite3.connect(str(DB_FILE))
            c.execute(
                "INSERT INTO sop_view_logs (attachment_id, worker_id, worker_name, workorder_id) VALUES (?, ?, ?, ?)",
                (str(attachment_id), worker_id or "", worker_name or "", str(workorder_id or "")),
            )
            c.commit()
            c.close()
    except Exception as e:
        logger.warning(f"SOP view log error: {e}")


def odoo_update_workorder_progress(client, workorder_id: int, qty: float, production_id: int,
                                   target_qty=None):
    """
    报工成功后，同步更新 Odoo 中工单/MO 的已完成数量 + 工单状态。
    - mrp.workorder.qty_produced 累加本次 qty
    - mrp.production.qty_produced = sum of all WO.qty_produced（覆盖同步，不累加）
      Odoo 的 MO.qty_produced 是计算字段（依赖 move_finished_ids.quantity）
      直接覆盖 finished move.quantity 让 MO.qty_produced 始终等于实际报工数
    - 如果 qty_produced >= qty_production，自动标记工单 done
    """
    if not workorder_id or qty <= 0:
        return {"ok": False, "error": "缺少参数"}
    new_wo_qty = None
    try:
        # 1) 更新工单（WO.qty_produced 是基础字段，必须写）
        wo_rows = client.read("mrp.workorder", [workorder_id],
                              ["id", "production_id", "qty_produced",
                               "qty_production", "state"])
        if not wo_rows:
            return {"ok": False, "error": f"工单 #{workorder_id} 不存在"}
        wo = wo_rows[0]
        actual_production_id = rel_id(wo.get("production_id"))
        if production_id and actual_production_id != int(production_id):
            return {"ok": False, "error": "工单与生产订单不匹配"}
        if wo.get("state") in ("done", "cancel"):
            return {"ok": False, "error": "工单已完成或已取消"}
        current_wo_qty = float(wo.get("qty_produced", 0))
        if target_qty is None:
            new_wo_qty = current_wo_qty + float(qty)
        else:
            # A retry may reach this function after the WO write succeeded but
            # a later MO read/write failed.  Never add the same report twice.
            new_wo_qty = max(current_wo_qty, float(target_qty))
        wo_vals = {"qty_produced": new_wo_qty}
        # 自动 mark done
        if new_wo_qty >= float(wo.get("qty_production", 0)) and wo.get("state") not in ("done", "cancel"):
            wo_vals["state"] = "done"
            wo_vals["date_finished"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        client.call("mrp.workorder", "write", [[workorder_id], wo_vals])
        logger.info(f"工单#{workorder_id} qty_produced {wo.get('qty_produced',0)} -> {new_wo_qty}")
        wo_check = client.read("mrp.workorder", [workorder_id], ["qty_produced"] )
        if not wo_check or abs(float(wo_check[0].get("qty_produced", 0)) - new_wo_qty) > 1e-6:
            return {"ok": False, "error": "工单已写入但回读数量不一致",
                    "new_qty": new_wo_qty}

        # 2) 同步 finished move：quantity = sum of all WO.qty_produced，state='done'
        #    Odoo 的 MO.qty_produced 是 computed field，依赖 move_finished_ids
        #    但只有 state='done' 的 finished move 才会被计入，所以必须设 state=done
        #    （实测 state='done' 不会让 MO 自动 done，MO state 由 qty_produced vs product_qty 决定）
        if production_id:
            all_wos = client.call("mrp.workorder", "search_read",
                [[("production_id", "=", production_id)]],
                {"fields": ["id", "qty_produced"]})
            total_produced = sum(float(w.get("qty_produced", 0)) for w in all_wos)

            mo_rows = client.read("mrp.production", [production_id],
                                  ["id", "move_finished_ids", "state",
                                   "product_qty", "qty_producing"])
            if not mo_rows:
                return {"ok": False, "error": f"生产订单 #{production_id} 不存在",
                        "new_qty": new_wo_qty}
            mo = mo_rows[0]
            fm_ids_list = mo.get("move_finished_ids") or []
            move_errors = []
            finished_uom_qty = 0.0
            if fm_ids_list:
                fm_data = client.read("stock.move", fm_ids_list,
                                      ["id", "product_uom_qty", "quantity", "state"])
                if fm_data:
                    finished_uom_qty = float(fm_data[0].get("product_uom_qty", 0) or 0)
            for fm_id in fm_ids_list:
                try:
                    client.call("stock.move", "write", [[fm_id], {
                        "quantity": total_produced,
                        "state": "done",
                    }])
                except Exception as e:
                    move_errors.append(f"成品移动 {fm_id}: {e}")
            if move_errors:
                return {"ok": False,
                        "error": "; ".join(move_errors),
                        "new_qty": new_wo_qty,
                        "total_produced": total_produced}
            logger.info(f"MO#{production_id} finished move 同步为 quantity={total_produced}, state=done")

            # 3) 更新 MO.qty_producing。分母使用成品移动的计划数量，
            # 不再写死 50，避免不同生产订单出现错误的待消耗数量。
            product_qty = float(mo.get("product_qty", 0) or 0)
            raw_qty = float(total_produced)
            denominator = finished_uom_qty or product_qty or 1.0
            if product_qty > 0 and raw_qty < product_qty:
                desired_should = product_qty - raw_qty
                new_qty_producing = raw_qty + desired_should * max(product_qty - raw_qty, 0) / denominator
            else:
                new_qty_producing = max(product_qty - raw_qty, 0)
            client.call("mrp.production", "write", [[production_id], {
                "qty_producing": new_qty_producing,
            }])
            logger.info(f"MO#{production_id} qty_producing={new_qty_producing:.2f} (UI 待消耗={int(max(product_qty - raw_qty, 0))})")

            # 回读关键字段，确认面板下一次查询拿到的是本次报工结果。
            mo_check = client.read("mrp.production", [production_id],
                                  ["qty_produced", "qty_producing"])
            if not mo_check:
                return {"ok": False, "error": "生产订单写入后无法回读",
                        "new_qty": new_wo_qty}
            mo_qty = float(mo_check[0].get("qty_produced", 0) or 0)
            if abs(mo_qty - total_produced) > 1e-6:
                return {"ok": False,
                        "error": f"生产订单已写入但回读产量不一致（期望 {total_produced:g}，实际 {mo_qty:g}）",
                        "new_qty": new_wo_qty,
                        "total_produced": total_produced}
        return {"ok": True, "new_qty": new_wo_qty}
    except Exception as e:
        logger.warning(f"Odoo 进度更新失败: {e}")
        result = {"ok": False, "error": str(e)}
        if new_wo_qty is not None:
            result["new_qty"] = new_wo_qty
        return result


def _direct_deduct_quant(client, product_id, code, actual_qty):
    """降级方案：直接扣 stock.quant（当 stock.move 找不到时）"""
    remaining = actual_qty
    quant_ids = odoo_call(client, "stock.quant", "search", [
        [("product_id", "=", product_id), ("location_id", "=", SRC_LOCATION_ID)]
    ])
    if quant_ids:
        quants = odoo_call(client, "stock.quant", "read", [quant_ids],
                           {"fields": ["id", "quantity"]})
        # 负库存 quant 不应参与“可扣数量”计算；否则 min(负数, actual_qty)
        # 会让 remaining 反而增加，造成错误的库存结果。
        positive_quants = [q for q in quants if float(q.get("quantity", 0) or 0) > 0]
        for q in positive_quants:
            if remaining <= 0:
                break
            qty = float(q["quantity"])
            take = min(qty, remaining)
            odoo_call(client, "stock.quant", "write", [[q["id"]], {
                "quantity": qty - take, "inventory_quantity": qty - take,
            }])
            remaining -= take
        if remaining > 0 and positive_quants:
            last = positive_quants[-1]
            # 上面的循环已经写过 last quant；重新读取当前值再扣剩余量，
            # 避免用旧值回写导致库存被“加回”。
            latest = odoo_call(client, "stock.quant", "read", [[last["id"]]],
                               {"fields": ["quantity"]})
            current_last_qty = float(latest[0].get("quantity", 0)) if latest else 0.0
            odoo_call(client, "stock.quant", "write", [[last["id"]], {
                "quantity": current_last_qty - remaining,
                "inventory_quantity": current_last_qty - remaining,
            }])
        elif remaining > 0:
            # 没有正库存时允许落到负库存，但数量计算仍保持精确。
            odoo_call(client, "stock.quant", "create", [{
                "product_id": product_id, "location_id": SRC_LOCATION_ID,
                "quantity": -remaining, "inventory_quantity": -remaining,
            }])
    else:
        odoo_call(client, "stock.quant", "create", [{
            "product_id": product_id, "location_id": SRC_LOCATION_ID,
            "quantity": -actual_qty, "inventory_quantity": -actual_qty,
        }])


def odoo_deduct_materials(materials, production_id=None, qty=0, idempotency_key=""):
    """
    报工物料扣减 + MO 进度同步（通过 stock.move，让 Odoo UI 显示正确）
    - 对每个 raw material: 找到对应 MO 的 stock.move，累加 quantity 并 _action_done
      Odoo 自动创建 stock.move.line 并扣减 stock.quant（不会双重扣减）
    - 对 finished product: 同样找到 MO 的 move_finished_ids，累加 quantity
      MO.qty_produced（计算字段）自动从 sum of finished moves 更新
    - MO.move_raw_ids 的 quantity 也会更新，组件 UI 显示正确的已消耗数量
    """
    if MOCK_MODE:
        logger.info(f"[MOCK] 模拟物料扣减: {len(materials)}项")
        return {"ok": True, "stock_move_ids": [900000 + i for i in range(len(materials))],
                "message": "模拟物料扣减成功", "meta": {"mode": "mock", "source": "fake_odoo"}}

    if idempotency_key:
        try:
            with DB_LOCK:
                c = sqlite3.connect(str(DB_FILE))
                row = c.execute(
                    "SELECT id FROM reports WHERE idempotency_key=? AND sync_status='odoo_synced'",
                    (idempotency_key,)).fetchone()
                c.close()
                if row:
                    return {"ok": True, "stock_move_ids": [], "message": "已扣减过（幂等）",
                            "meta": {"mode": "real", "source": "idempotent"}}
        except Exception:
            pass

    client = get_odoo()
    # 这里保存真正更新过的 stock.move ID；此前误把 product_id 写入此字段，
    # 导致排查时误认为 Odoo 移动记录与报工记录不一致。
    stock_move_ids = []
    successful_material_count = 0
    errors = []

    # 0) 预加载 MO 的 raw moves（按 product_id 分组）
    raw_move_ids_by_pid = {}
    if production_id:
        try:
            mo = client.read("mrp.production", [production_id], ["move_raw_ids"])
            raw_ids = mo[0].get("move_raw_ids", []) if mo else []
            if raw_ids:
                rms = client.read("stock.move", raw_ids, ["id", "product_id", "state"])
                for rm in rms:
                    pid = rel_id(rm.get("product_id"))
                    if pid not in raw_move_ids_by_pid:
                        raw_move_ids_by_pid[pid] = []
                    raw_move_ids_by_pid[pid].append(rm["id"])
        except Exception as e:
            logger.warning(f"读 MO raw moves 失败: {e}")

    for mat in materials:
        product_id = mat.get("productId", 0)
        actual_qty = mat.get("actualQty", 1)
        code = mat.get("defaultCode", "?")

        if not product_id or actual_qty <= 0:
            errors.append(f"物料 {code}: 无效参数")
            continue

        try:
            _ensure_negative_stock_ok(client, product_id)

            # 1) 更新 MO 的 raw stock.move.quantity
            #    只改 quantity，不改 state（否则 MO 会自动 done）
            rm_ids = raw_move_ids_by_pid.get(product_id, [])
            if not rm_ids:
                errors.append(f"物料 {code}: 不属于生产订单 #{production_id} 的原料移动")
                continue
            updated_move_ids = []
            with MATERIAL_QUANTITY_LOCK:
                for rm_id in rm_ids:
                    rm = odoo_call(
                        client, "stock.move", "read", [[rm_id], [
                            "product_uom_qty", "quantity", "should_consume_qty",
                        ]]
                    )
                    if not rm:
                        raise ValueError(f"原料移动 {rm_id} 不存在")
                    # stock.move.quantity can contain reserved stock, so it is
                    # not a safe accumulation base. Odoo's correct remaining
                    # amount gives the consumed total before this report.
                    planned_qty = float(
                        rm[0].get("product_uom_qty", 0) or 0
                    )
                    remaining_qty = OdooRemainingQuantityFix.remaining_consumption_qty(rm[0])
                    consumed_before = max(planned_qty - remaining_qty, 0.0)
                    consumed_after = consumed_before + float(actual_qty)
                    odoo_call(client, "stock.move", "write", [[rm_id], {
                        "quantity": consumed_after,
                        "picked": True,
                    }])
                    check = odoo_call(client, "stock.move", "read", [[rm_id], [
                        "quantity", "should_consume_qty",
                    ]])
                    if not check or abs(
                        float(check[0].get("quantity", 0) or 0) - consumed_after
                    ) > 1e-6:
                        raise ValueError(
                            f"原料移动 {rm_id} 的实际消耗数量写入后不一致"
                        )
                    if abs(
                        float(check[0].get("should_consume_qty", 0) or 0)
                        - remaining_qty
                    ) > 1e-6:
                        raise ValueError(
                            f"原料移动 {rm_id} 的待消耗数量被意外改变"
                        )
                    updated_move_ids.append(rm_id)

                # Keep the stock deduction atomic with the cumulative display
                # quantity so concurrent reports cannot overwrite each other.
                _direct_deduct_quant(client, product_id, code, actual_qty)

            logger.info(f"物料 {code}({product_id}): 已扣减 {actual_qty}")
            stock_move_ids.extend(updated_move_ids)
            successful_material_count += 1

        except Exception as e:
            errors.append(f"物料 {code}: {e}")
            logger.error(f"物料 {code} 扣减失败: {e}")

    # ===== 处理 finished product 已移到 odoo_update_workorder_progress =====
    # 这里不再重复更新 finished move，由 odoo_update_workorder_progress 同步
    # （覆盖同步，保证 MO.qty_produced 始终 = sum(WO.qty_produced)）

    all_failed = len(materials) > 0 and successful_material_count == 0
    partial = len(errors) > 0 and successful_material_count > 0

    if all_failed:
        return {"ok": False, "stock_move_ids": [], "errors": errors,
                "message": f"全部 {len(materials)} 项物料扣减失败",
                "meta": {"mode": "real", "source": "odoo"}}
    if partial:
        return {"ok": True, "partial": True, "stock_move_ids": stock_move_ids, "errors": errors,
                "message": f"{successful_material_count} 项物料已扣减，{len(errors)} 项失败",
                "meta": {"mode": "real", "source": "odoo"}}
    return {"ok": True, "stock_move_ids": stock_move_ids,
            "message": f"已完成 {len(stock_move_ids)} 项物料库存扣减",
            "meta": {"mode": "real", "source": "odoo"}}


_NEGATIVE_STOCK_LOCK = threading.Lock()
_NEGATIVE_STOCK_DONE = set()


def _ensure_negative_stock_ok(client, product_id):
    """为产品模板启用负库存（线程安全）"""
    # Keep the check and the one-time Odoo writes in the same critical section.
    # This function is called only once per material in the deduction path, so
    # avoiding duplicate remote writes is worth the short lock hold.
    with _NEGATIVE_STOCK_LOCK:
        if product_id in _NEGATIVE_STOCK_DONE:
            return
        try:
            rows = odoo_call(client, "product.product", "search_read",
                             [[("id", "=", product_id)]],
                             {"fields": ["product_tmpl_id"], "limit": 1})
            if rows:
                tmpl = rows[0].get("product_tmpl_id", 0)
                if isinstance(tmpl, (list, tuple)):
                    tmpl = tmpl[0]
                odoo_call(client, "product.template", "write",
                          [[tmpl], {"allow_negative_stock": True}])
                _NEGATIVE_STOCK_DONE.add(product_id)
                logger.info(f"物料 #{product_id} tmpl#{tmpl} 负库存已启用")
        except Exception:
            pass



def server_reset_all():
    """完整重置：清空 SQLite 报工记录 + 重置 Odoo 库存/MO/WO 到初始值"""
    summary = {"sqlite_cleared": 0, "materials_cleared": 0,
               "bom_reset": 0, "mo_reset": 0, "wo_reset": 0}

    try:
        with DB_LOCK:
            c = sqlite3.connect(str(DB_FILE))
            cur = c.execute("SELECT COUNT(*) FROM reports")
            summary["sqlite_cleared"] = cur.fetchone()[0]
            # report_materials 没有 ON DELETE CASCADE，必须先显式清理，避免留下孤立物料记录。
            try:
                summary["materials_cleared"] = c.execute(
                    "SELECT COUNT(*) FROM report_materials"
                ).fetchone()[0]
                c.execute("DELETE FROM report_materials")
            except sqlite3.OperationalError:
                pass
            c.execute("DELETE FROM reports")
            try:
                c.execute("DELETE FROM sop_view_logs")
            except sqlite3.OperationalError:
                pass
            c.commit()
            c.close()
    except Exception as e:
        logger.warning(f"清空 SQLite 失败: {e}")

    client = get_odoo()
    if not client:
        return summary

    ALL_BOM_CODES = [
        "P04725", "P04726", "P05346", "P05347", "P05348",
        "P05350", "P05351", "P05352", "P05353", "P05931",
    ]
    PROD_BEFORE_QTY = {
        "P04725": 200, "P04726": 200, "P05346": 200, "P05347": 200,
        "P05348": 200, "P05350": 200, "P05351": 200, "P05352": 200,
        "P05353": 200, "P05930": 50, "P05931": 100,
    }

    def _call(model, method, args=None, kwargs=None):
        return odoo_call(client, model, method, args, kwargs)

    def _reset_quant(product_id, location_id, target_qty):
        qids = _call("stock.quant", "search", [[("product_id", "=", product_id), ("location_id", "=", location_id)]])
        if qids:
            for qid in qids[1:]:
                _call("stock.quant", "write", [[qid], {"quantity": 0, "inventory_quantity": 0}])
            _call("stock.quant", "write", [[qids[0]], {"quantity": target_qty, "inventory_quantity": target_qty}])
        else:
            _call("stock.quant", "create", [{"product_id": product_id, "location_id": location_id,
                                            "quantity": target_qty, "inventory_quantity": target_qty}])

    for code in ALL_BOM_CODES + ["P05930"]:
        prod = _call("product.product", "search_read",
                      [[("default_code", "=", code)]], {"fields": ["id"], "limit": 1})
        if not prod:
            continue
        pid = prod[0]["id"]
        prod_before = PROD_BEFORE_QTY.get(code, 200)
        try:
            _reset_quant(pid, 8, 100)
            _reset_quant(pid, 17, prod_before)
            summary["bom_reset"] += 1
        except Exception as e:
            logger.warning(f"重置 {code} 库存失败: {e}")

    mos = _call("mrp.production", "search", [[("state", "not in", ["done", "cancel"])]])
    for mo_id in mos:
        try:
            mo = _call("mrp.production", "read", [[mo_id]],
                       {"fields": ["move_raw_ids", "move_finished_ids", "product_qty"]})
            for rm_id in (mo[0].get("move_raw_ids") or []):
                try:
                    _call("stock.move", "write", [[rm_id], {"quantity": 0}])
                except Exception:
                    pass
            for fm_id in (mo[0].get("move_finished_ids") or []):
                try:
                    _call("stock.move", "write", [[fm_id], {"quantity": 0, "state": "assigned"}])
                except Exception:
                    pass
            try:
                _call("mrp.production", "write", [[mo_id], {"qty_producing": mo[0].get("product_qty", 0)}])
            except Exception:
                pass
            summary["mo_reset"] += 1
        except Exception as e:
            logger.warning(f"重置 MO#{mo_id} 失败: {e}")

    wos = _call("mrp.workorder", "search", [[("state", "not in", ["done", "cancel"])]])
    for wid in wos:
        try:
            _call("mrp.workorder", "write", [[wid], {"qty_produced": 0}])
            summary["wo_reset"] += 1
        except Exception:
            pass

    # 清零 WO 可能触发 Odoo 重算成品移动状态，最后再恢复初始的 assigned 状态。
    for mo_id in mos:
        try:
            mo = _call("mrp.production", "read", [[mo_id]], {"fields": ["move_finished_ids"]})
            for fm_id in (mo[0].get("move_finished_ids") or []) if mo else []:
                _call("stock.move", "write", [[fm_id], {"quantity": 0, "state": "assigned"}])
        except Exception:
            pass

    # Odoo 已重置后立即失效服务端缓存，避免面板继续显示旧进度/BOM。
    _invalidate_runtime_caches()

    # Persist the reset event so a separately running panel process drops its
    # in-memory Odoo caches on the next request as well.
    try:
        RESET_MARKER_FILE.touch()
    except OSError as marker_err:
        logger.warning(f"写入重置标记失败: {marker_err}")

    # This cache is used by report validation and must not retain identifiers
    # from the pre-reset dashboard.
    with _order_ids_lock:
        _order_ids_cache["ids"] = set()
        _order_ids_cache["ts"] = 0
        _order_ids_cache["marker"] = 0

    return summary


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    @staticmethod
    def _allowed_origin(origin):
        if not origin:
            return None
        for prefix in ("http://192.168.", "http://127.0.0.", "http://localhost"):
            if origin.startswith(prefix):
                return origin
        return None

    def end_headers(self):
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        qs = urlparse(self.path).query
        params = {}
        if qs:
            for pair in qs.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = v

        if path.startswith("/api/"):
            return self._route_get_api(path, params)
        ext = os.path.splitext(path)[1].lower()
        if path == "/":
            self.send_response(301)
            self.send_header("Location", "/worker-report.html")
            self.end_headers()
            return
        if ext in WHITE_EXT:
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _route_get_api(self, path, params):
        if path == "/api/health":
            self.write_json({"ok": True, "mode": get_odoo_mode()})
        elif path == "/api/workers":
            workers = load_workers()
            self.write_json({"ok": True, "data": workers,
                             "meta": {"mode": get_odoo_mode(), "count": len(workers)}})
        elif path == "/api/reports":
            reports = load_reports()
            self.write_json({"ok": True, "data": [_normalize_report(r) for r in reports]})
        elif path == "/api/report-stats":
            payload = self.report_stats_payload()
            status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR
            self.write_json(payload, status=status)
        elif path == "/api/operations":
            ops = get_operations()
            self.write_json({"ok": True, "data": ops,
                             "meta": {"mode": get_odoo_mode(), "count": len(ops)}})
        elif path == "/api/workorders":
            try:
                wos = get_workorders_data()
                self.write_json({"ok": True, "data": wos,
                                 "meta": {"mode": get_odoo_mode(), "count": len(wos)}})
            except Exception as e:
                self.write_json({"ok": False, "error": f"获取工单失败: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/bom":
            host_type = params.get("hostType", params.get("host_type", ""))
            workorder_id = params.get("workorderId", params.get("workorder_id", ""))
            nocache = params.get("nocache", "0") in ("1", "true", "yes")
            if not host_type and workorder_id:
                # 根据工单 ID 推断主机类型
                try:
                    wos = get_workorders_data()
                    for wo in wos:
                        if str(wo.get("workorderId")) == str(workorder_id):
                            host_type = wo.get("hostType", "")
                            break
                except Exception:
                    pass
            if host_type not in ("tape", "splitter"):
                self.write_json({"ok": False, "error": "需要指定 hostType=tape 或 hostType=splitter"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            try:
                # nocache=1 时强制清掉缓存，重新从 Odoo 拉最新
                if nocache:
                    with _BOM_CACHE_LOCK:
                        _BOM_CACHE["data"] = None
                        _BOM_CACHE["ts"] = 0
                        _BOM_CACHE["key"] = None
                items = get_bom_data(host_type)
                self.write_json({"ok": True, "data": items,
                                 "meta": {"mode": get_odoo_mode(), "hostType": host_type,
                                          "count": len(items), "nocache": nocache}})
            except Exception as e:
                logger.error(f"BOM 查询异常: {e}")
                self.write_json({"ok": False, "error": f"BOM数据加载失败: {e}",
                                 "data": [], "meta": {"mode": get_odoo_mode(), "hostType": host_type}},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
        elif path == "/api/order-summary":
            # 订单进度摘要（从工单汇总 MO 进度）
            try:
                client = get_odoo()
                mo_domain = [("state", "not in", ["done", "cancel"])]
                mo_ids = client.call("mrp.production", "search", [mo_domain])
                if not mo_ids:
                    self.write_json({"ok": True, "data": [], "meta": {"mode": get_odoo_mode(), "count": 0}})
                    return
                mos = client.read("mrp.production", mo_ids, [
                    "name", "origin", "state", "product_id", "product_qty",
                    "qty_produced", "date_start", "write_date"
                ])
                # 拉所有未完成工单
                wo_domain = [("production_id", "in", mo_ids),
                             ("state", "not in", ["done", "cancel"])]
                wo_ids = client.call("mrp.workorder", "search", [wo_domain])
                wo_by_mo = {}
                if wo_ids:
                    wos = client.read("mrp.workorder", wo_ids, [
                        "id", "name", "production_id", "state", "qty_produced", "qty_production",
                        "workcenter_id", "operation_id"
                    ])
                    for w in wos:
                        pid = rel_id(w.get("production_id"))
                        if pid not in wo_by_mo:
                            wo_by_mo[pid] = []
                        wo_by_mo[pid].append({
                            "workorderId": w["id"],
                            "name": w.get("name", ""),
                            "state": w.get("state", ""),
                            "qtyProduced": float(w.get("qty_produced", 0) or 0),
                            "qtyProduction": float(w.get("qty_production", 0) or 0),
                            "workcenterName": rel_name(w.get("workcenter_id"), ""),
                        })
                items = []
                for mo in mos:
                    mo_id = mo["id"]
                    pid = rel_id(mo.get("product_id"))
                    wos = wo_by_mo.get(mo_id, [])
                    # MO 实际进度 = 已完成工单的 qty_produced 之和 / qty_production 之和
                    total_done = sum(w["qtyProduced"] for w in wos)
                    total_target = sum(w["qtyProduction"] for w in wos) or float(mo.get("product_qty", 1))
                    progress_pct = (total_done / total_target * 100) if total_target > 0 else 0
                    items.append({
                        "productionId": mo_id,
                        "productionName": mo.get("name", ""),
                        "productName": rel_name(mo.get("product_id"), ""),
                        "productQty": float(mo.get("product_qty", 0) or 0),
                        "qtyProduced": total_done,  # 从工单汇总（Odoo qty_produced 是计算字段）
                        "progress": round(progress_pct, 1),
                        "state": mo.get("state", ""),
                        "stateLabel": MO_STATE_MAP.get(mo.get("state", ""), mo.get("state", "")),
                        "workorders": wos,
                    })
                self.write_json({"ok": True, "data": items,
                                 "meta": {"mode": get_odoo_mode(), "count": len(items)}})
            except Exception as e:
                logger.error(f"order-summary 异常: {e}")
                self.write_json({"ok": False, "error": f"订单摘要失败: {e}",
                                 "data": [], "meta": {"mode": get_odoo_mode()}},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        # ---- ESOP 模块 API ----
        elif path == "/api/sop/list":
            wo_id_str = params.get("workorderId", params.get("workorder_id", ""))
            if not wo_id_str:
                self.write_json({"ok": False, "error": "缺少 workorderId 参数"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            try:
                wo_id = int(wo_id_str)
                sop_list = get_sop_for_workorder(wo_id)
                self.write_json({"ok": True, "data": sop_list, "meta": {"mode": get_odoo_mode(), "count": len(sop_list)}})
            except Exception as e:
                self.write_json({"ok": False, "error": f"SOP查询失败: {e}"},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path == "/api/sop/download":
            wo_id_str = params.get("workorderId", "")
            kind = params.get("type", "worksheet")
            if not wo_id_str:
                self.send_error(HTTPStatus.BAD_REQUEST, "缺少 workorderId")
                return
            try:
                wo_id = int(wo_id_str)
                file_data = get_sop_download(wo_id, kind)
                if file_data is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "该工单无 SOP 附件")
                    return
                mime, b64data, filename = file_data
                import base64
                raw = base64.b64decode(b64data) if b64data else b""
                # 中文文件名用 RFC 5987 编码
                from urllib.parse import quote
                ascii_name = "SOP.pdf" if kind == "worksheet" else "SOP.png"
                encoded_name = quote(filename)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Disposition",
                                 f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(raw)
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST, "workorderId 必须为数字")
            except Exception as e:
                logger.error(f"SOP下载异常: {e}")
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        # 请求体大小限制 64KB
        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body too large")
            return
        if not check_auth(self):
            self.write_json({"ok": False, "error": "未授权：缺少或无效的 API Key"},
                            status=HTTPStatus.UNAUTHORIZED)
            return
        if path == "/api/reports":
            self.handle_report_post()
        elif path == "/api/workers":
            self.handle_worker_post()
        elif path == "/api/reset":
            # 远程触发：清空本地 SQLite + Odoo 库存/MO/WO 重置
            # 可选 confirm=true 才执行（防止误触）
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            if not body.get("confirm"):
                self.write_json({"ok": False, "error": "需要 confirm=true 才执行"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            logger.warning(f"[API] 远程触发 reset from {self.client_address}")
            try:
                summary = server_reset_all()
                self.write_json({"ok": True, "data": summary})
            except Exception as e:
                logger.error(f"[API] reset 失败: {e}")
                self.write_json({"ok": False, "error": str(e)},
                                status=HTTPStatus.INTERNAL_SERVER_ERROR)
        elif path == "/api/sop/view-log":
            # SOP 查看日志上报（无需 token，日志而已）
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
                log_sop_view(
                    body.get("attachmentId", ""),
                    body.get("workerId", ""),
                    body.get("workerName", ""),
                    body.get("workorderId", ""),
                )
                self.write_json({"ok": True})
            except Exception as e:
                self.write_json({"ok": False, "error": str(e)})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    # ---- API 实现 ----

    def order_summary_payload(self):
        try:
            data = load_dashboard()
            orders = [{
                "id": row.get("order", ""),
                "customerCode": row.get("customerCode", ""),
                "customer": row.get("customer", ""),
                "product": row.get("machine", ""),
                "code": row.get("code", ""),
                "spec": row.get("spec", ""),
                "qty": row.get("qty", ""),
                "uom": row.get("uom", ""),
                "remaining": row.get("remaining", ""),
                "remark": row.get("remark", ""),
                "date": row.get("date", ""),
                "updated": row.get("updated", ""),
                "priority": row.get("priority", ""),
                "status": row.get("delivery", ""),
            } for row in data.get("deliveryRows", [])]
            return {"ok": True, "data": orders,
                    "meta": {"mode": get_odoo_mode(), "source": "odoo"}}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def handle_report_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            report = json.loads(body)

            mode = get_odoo_mode()

            # === 幂等检查（仅 sync_status=odoo_synced 才短路，允许失败重试） ===
            idempotency_key = report.get("idempotencyKey", "")
            if idempotency_key:
                existing = db_get_report_by_idempotency(idempotency_key)
                if existing and existing.get("sync_status") == "odoo_synced":
                    logger.info(f"幂等请求: {idempotency_key} - 返回已有结果")
                    self.write_json({
                        "ok": True,
                        "data": _normalize_report(existing),
                        "meta": {"mode": mode, "source": "idempotent_replay",
                                 "message": "该报工已处理过，返回已有结果"}
                    })
                    return

            # === 输入校验 ===
            required = ["workerId", "workerName", "operation", "qty", "date", "time"]
            for field in required:
                if not report.get(field):
                    self.write_json({"ok": False, "error": f"缺少必填字段: {field}"},
                                    status=HTTPStatus.BAD_REQUEST)
                    return

            worker_id = str(report["workerId"])
            if worker_id not in get_valid_worker_ids():
                self.write_json({"ok": False, "error": f"工人 {worker_id} 不存在"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # 新字段: 工单ID
            workorder_id = str(report.get("workorderId", report.get("orderId", "")))
            production_id = str(report.get("productionId", ""))

            # A report without an Odoo work order can only change the local
            # report table and cannot be synchronized to a production order.
            # Reject it before any material deduction takes place.
            if (not workorder_id.isdigit() or int(workorder_id) <= 0
                    or not production_id.isdigit() or int(production_id) <= 0):
                self.write_json({"ok": False, "error": "请先选择有效的工单后再报工"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            if mode == "real":
                try:
                    check_client = get_odoo()
                    check_rows = check_client.read(
                        "mrp.workorder", [int(workorder_id)],
                        ["production_id", "state", "name", "product_id", "workcenter_id"]
                    )
                    if not check_rows:
                        raise ValueError("工单不存在")
                    actual_production_id = rel_id(check_rows[0].get("production_id"))
                    if actual_production_id != int(production_id):
                        raise ValueError("工单与生产订单不匹配")
                    if check_rows[0].get("state") in ("done", "cancel"):
                        raise ValueError("工单已完成或已取消")
                except Exception as check_err:
                    self.write_json({"ok": False, "error": f"工单校验失败: {check_err}"},
                                    status=HTTPStatus.BAD_REQUEST)
                    return

            operation = str(report["operation"])
            op_info = OPERATION_MAP.get(operation)
            if not op_info:
                self.write_json({"ok": False, "error": f"无效工序: {operation}"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if mode == "real" and op_info.get("mockOnly"):
                self.write_json({"ok": False, "error": "测试工序仅可在 Mock 模式使用"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            if not worker_allows_operation(worker_id, operation):
                self.write_json({
                    "ok": False,
                    "error": "当前工人未绑定该工序，不能提交报工",
                }, status=HTTPStatus.FORBIDDEN)
                return
            if mode == "real":
                workorder_view = {
                    "workorderName": check_rows[0].get("name", ""),
                    "hostType": workorder_host_type(
                        check_rows[0].get("product_id"),
                        check_rows[0].get("workcenter_id"),
                    ),
                }
                if not operation_matches_workorder(op_info, workorder_view):
                    self.write_json({
                        "ok": False,
                        "error": "所选工单不属于当前工序",
                    }, status=HTTPStatus.BAD_REQUEST)
                    return

            qty = report["qty"]
            # Production reports are counted in whole units. Reject decimal
            # quantities at the API boundary instead of letting a fractional
            # value propagate into WO/MO quantities and Odoo inverse fields.
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                self.write_json({"ok": False, "error": "Report quantity must be an integer"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            qty_value = float(qty)
            if not math.isfinite(qty_value) or qty_value <= 0 or not qty_value.is_integer():
                self.write_json({"ok": False, "error": "Report quantity must be a positive integer"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            qty = int(qty_value)
            report["qty"] = qty
            if not isinstance(qty, (int, float)) or qty <= 0:
                self.write_json({"ok": False, "error": "数量必须是正数"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # === 物料校验 ===
            materials = report.get("materials", [])
            host_type = op_info.get("hostType")
            if materials:
                # 校验每种物料的 actualQty > 0
                # 注意：物料清单由前端 BOM 弹窗从 Odoo mrp.bom 动态拉取，已保证属于当前主机类型，
                #       服务端不再硬编码 TAPE/SPLITTER BOM 校验，避免 Odoo 加新物料时拦截。
                for mat in materials:
                    code = mat.get("defaultCode", "")
                    actual_qty = mat.get("actualQty", 0)
                    if not isinstance(actual_qty, (int, float)) or actual_qty <= 0:
                        self.write_json({"ok": False, "error": f"物料 {code} 实际使用数量必须为正数"},
                                        status=HTTPStatus.BAD_REQUEST)
                        return
                    if not mat.get("productId"):
                        self.write_json({"ok": False, "error": f"物料 {code} 缺少产品ID"},
                                        status=HTTPStatus.BAD_REQUEST)
                        return

            # === 日期格式校验 ===
            date_str = str(report["date"])
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                self.write_json({"ok": False, "error": "日期格式错误，需要 YYYY-MM-DD"},
                                status=HTTPStatus.BAD_REQUEST)
                return

            # 业务唯一键在写入 Odoo 前检查。若上一次请求已经完成/部分完成，
            # 不能再次执行物料扣减，否则重试会造成库存重复扣减。
            existing_workorder_report = db_get_report_by_workorder(
                worker_id, workorder_id, date_str, operation
            )
            retry_existing_report = None
            if existing_workorder_report:
                old_status = existing_workorder_report.get("sync_status", "local")
                old_error = existing_workorder_report.get("error_message", "")
                if old_status in ("odoo_partial", "odoo_failed"):
                    # A retry only re-runs the WO/MO progress synchronization.
                    # Material deductions from the original attempt are never
                    # repeated, even when the client sends the BOM again.
                    retry_existing_report = existing_workorder_report
                else:
                    status_text = {
                        "odoo_synced": "已完整同步",
                        "odoo_partial": "仅部分同步",
                        "odoo_failed": "同步失败",
                    }.get(old_status, old_status or "本地已保存")
                    detail = f"（{old_error}）" if old_error else ""
                    self.write_json({
                        "ok": False,
                        "error": f"该工人今天已对该工单/工序报工（{status_text}）{detail}，为避免重复扣库存未再次提交",
                        "data": _normalize_report(existing_workorder_report),
                    }, status=HTTPStatus.CONFLICT)
                    return

            # === 构建记录 ===
            report["id"] = str(uuid.uuid4())
            report["timestamp"] = int(datetime.now(LOCAL_TZ).timestamp() * 1000)
            report.setdefault("operationLabel", op_info.get("name", report["operation"]))
            report.setdefault("qualified", qty)
            report.setdefault("hours", 0)
            report.setdefault("remark", "")
            report.setdefault("workerTeam", "")
            report.setdefault("orderCustomer", "")
            report.setdefault("orderProduct", "")
            report.setdefault("productionId", production_id)
            report.setdefault("workorderId", workorder_id)
            report.setdefault("odooEmployeeId", report.get("odooEmployeeId", 0))
            report.setdefault("idempotencyKey", idempotency_key)
            report.setdefault("odooReportId", "")
            report.setdefault("odooStockMoveIds", "")
            report.setdefault("syncStatus", "local")
            report.setdefault("materialSyncStatus", "unknown")
            report.setdefault("odooProgressQty", None)
            report.setdefault("errorMessage", "")

            retry_material_status = "not_required"
            retry_material_required = False
            retry_progress_target = None
            if retry_existing_report:
                # Reuse the original report row and its original quantities and
                # identifiers.  The incoming payload is only a retry trigger.
                report["id"] = retry_existing_report["id"]
                report["qty"] = retry_existing_report["qty"]
                qty = retry_existing_report["qty"]
                report["productionId"] = retry_existing_report.get("production_id", production_id)
                report["workorderId"] = retry_existing_report.get("workorder_id", workorder_id)
                report["idempotencyKey"] = retry_existing_report.get("idempotency_key", idempotency_key)
                report["odooReportId"] = retry_existing_report.get("odoo_report_id", "")
                report["odooStockMoveIds"] = retry_existing_report.get("odoo_stock_move_ids", "")
                retry_material_status = retry_existing_report.get("material_sync_status", "unknown")
                if retry_material_status == "unknown" and old_status == "odoo_partial":
                    # Rows written before material_sync_status was introduced
                    # used partial specifically for "stock done, progress
                    # failed".  Preserve that retry behavior for legacy rows.
                    retry_material_status = "synced"
                retry_material_required = retry_material_status not in ("not_required", "")
                stored_progress_qty = retry_existing_report.get("odoo_progress_qty")
                try:
                    if stored_progress_qty not in (None, ""):
                        retry_progress_target = float(stored_progress_qty)
                except (TypeError, ValueError):
                    retry_progress_target = None
                # The original material list remains in report_materials for
                # audit purposes, but must never be sent to Odoo again.
                materials = []

            # 兼容旧格式: orderId 用 report UUID 避免唯一约束冲突
            if not report.get("orderId") or report.get("orderId") == "":
                report["orderId"] = f"direct-{report['id'][:8]}"

            # === Mock 模式 ===
            if mode == "mock":
                if db_add_report(report, materials):
                    saved = db_get_report(report["id"]) or report
                    result = _normalize_report(saved) if isinstance(saved, dict) else saved
                    self.write_json({
                        "ok": True,
                        "data": result,
                        "meta": {
                            "mode": "mock",
                            "source": "fake_odoo",
                            "message": "模拟报工成功，未写入 Odoo",
                        },
                    })
                else:
                    self.write_json({
                        "ok": False,
                        "error": "重复报工：该工人已对此工单、此工序报过工"
                    }, status=HTTPStatus.CONFLICT)
                return

            # === 真实模式 - Odoo 写入 ===
            # 调用 Odoo 物料扣减（不依赖工单）
            odoo_result = None
            if materials and not MOCK_MODE:
                try:
                    odoo_result = odoo_deduct_materials(
                        materials=materials,
                        production_id=int(production_id) if str(production_id).isdigit() else None,
                        qty=qty,
                        idempotency_key=idempotency_key,
                    )
                    logger.info(f"物料扣减结果: {odoo_result.get('message','')}")
                except Exception as e:
                    logger.error(f"物料扣减异常: {e}")
                    odoo_result = {"ok": False, "error": str(e)}
            elif retry_existing_report:
                odoo_result = {
                    "ok": retry_material_status in ("not_required", "synced"),
                    "skipped": True,
                    "message": "重试仅同步工单进度，未重复扣减物料",
                }

            # 报工后同步 Odoo 中 MO/工单的 qty_produced + 状态
            progress_result = None
            if not MOCK_MODE and workorder_id:
                try:
                    client = get_odoo()
                    progress_result = odoo_update_workorder_progress(
                        client=client,
                        workorder_id=int(workorder_id) if str(workorder_id).isdigit() else 0,
                        qty=qty,
                        production_id=int(production_id) if str(production_id).isdigit() else 0,
                        **({"target_qty": retry_progress_target}
                           if retry_progress_target is not None else {}),
                    )
                    logger.info(f"Odoo 进度同步: {progress_result}")
                except Exception as e:
                    logger.warning(f"Odoo 进度同步异常: {e}")
                    progress_result = {"ok": False, "error": str(e)}

            # 只有所有需要写入 Odoo 的部分都成功，才能标记为完整同步。
            # 之前只要物料扣减成功就写成 odoo_synced，导致“库存已变、MO/WO 未变”
            # 被面板误显示为成功。
            report["odooReportId"] = str(workorder_id)
            report["odooStockMoveIds"] = json.dumps(
                (odoo_result or {}).get("stock_move_ids", []), ensure_ascii=False
            )
            if retry_existing_report:
                # Preserve the original material outcome while retrying only
                # the progress side.  A partial/failed material sync can never
                # be promoted to fully synced by a progress-only retry.
                report["odooStockMoveIds"] = retry_existing_report.get("odoo_stock_move_ids", "")
                report["materialSyncStatus"] = retry_material_status
            elif not materials:
                report["materialSyncStatus"] = "not_required"
            elif odoo_result and odoo_result.get("partial"):
                report["materialSyncStatus"] = "partial"
            elif odoo_result and odoo_result.get("ok"):
                report["materialSyncStatus"] = "synced"
            else:
                report["materialSyncStatus"] = "failed"

            material_required = bool(materials) or retry_material_required
            if retry_existing_report:
                material_ok = retry_material_status in ("not_required", "synced")
                material_partial = retry_material_status == "partial"
                material_write_ok = retry_material_status in ("synced", "partial")
            else:
                material_ok = (not material_required) or bool(odoo_result and odoo_result.get("ok"))
                material_partial = bool(odoo_result and odoo_result.get("partial"))
                material_write_ok = bool(odoo_result and odoo_result.get("ok"))
            progress_ok = bool(progress_result and progress_result.get("ok"))
            # 历史兼容：旧客户端可能在没有 workorder/production ID 时把物料写入 Odoo。
            # 新请求已在上方拒绝这种数据；这里即使被旧进程处理，也不能再标记为完整同步。
            if not str(workorder_id).isdigit() or not str(production_id).isdigit():
                progress_ok = False
                errors_list = ["缺少有效的工单/生产订单 ID，无法同步制造订单进度"]
            else:
                errors_list = []
            if material_required and not material_ok:
                if retry_existing_report:
                    material_errors = [
                        retry_existing_report.get("error_message") or "物料同步尚未完成，请先处理物料后再重试"
                    ]
                else:
                    material_errors = (odoo_result or {}).get("errors") or [(odoo_result or {}).get("error", "物料库存扣减失败")]
                errors_list.extend(material_errors if isinstance(material_errors, list) else [material_errors])
            elif material_partial:
                if retry_existing_report:
                    partial_errors = [
                        retry_existing_report.get("error_message") or "部分物料库存扣减失败"
                    ]
                else:
                    partial_errors = odoo_result.get("errors") or ["部分物料库存扣减失败"]
                errors_list.extend(partial_errors if isinstance(partial_errors, list) else [partial_errors])
            if not progress_ok:
                progress_error = (progress_result or {}).get("error", "制造订单/工序进度同步失败")
                errors_list.append(progress_error)

            if material_ok and progress_ok and not material_partial:
                report["syncStatus"] = "odoo_synced"
                report["errorMessage"] = ""
            elif progress_ok or (material_required and material_write_ok):
                # 任一 Odoo 子步骤已成功但另一子步骤失败，都属于部分同步。
                # 例如：库存已经扣减、但 WO/MO 回读失败，不能标成“未同步”。
                report["syncStatus"] = "odoo_partial"
                report["errorMessage"] = "; ".join(str(e) for e in errors_list)
            else:
                report["syncStatus"] = "odoo_failed"
                report["errorMessage"] = "; ".join(str(e) for e in errors_list) or "Odoo 同步失败"

            progress_qty = (progress_result or {}).get("new_qty")
            if progress_qty is not None:
                report["odooProgressQty"] = progress_qty
            elif retry_existing_report:
                report["odooProgressQty"] = retry_existing_report.get("odoo_progress_qty")

            # 任一 Odoo 写入成功后都失效相关缓存，避免面板显示旧数据。
            if material_ok or progress_ok:
                _invalidate_runtime_caches()

            if retry_existing_report:
                saved_ok = db_update_report_sync(
                    report_id=report["id"],
                    sync_status=report["syncStatus"],
                    error_message=report["errorMessage"],
                    odoo_report_id=report.get("odooReportId"),
                    odoo_stock_move_ids=report.get("odooStockMoveIds"),
                    material_sync_status=report.get("materialSyncStatus"),
                    odoo_progress_qty=report.get("odooProgressQty"),
                )
            else:
                saved_ok = db_add_report(report, materials)

            if saved_ok:
                saved = db_get_report(report["id"]) or report
                result = _normalize_report(saved) if isinstance(saved, dict) else saved

                if report["syncStatus"] == "odoo_synced":
                    msg = "报工成功，Odoo 物料库存与制造订单进度已同步"
                elif report["syncStatus"] == "odoo_partial":
                    msg = "报工已保存，但 Odoo 仅部分同步：" + report["errorMessage"]
                else:
                    msg = "报工已保存，但 Odoo 未完成同步：" + report["errorMessage"]

                self.write_json({
                    "ok": True,
                    "data": result,
                    "meta": {
                        "mode": "real",
                        "source": "odoo",
                        "message": msg,
                        "odoo_result": odoo_result,
                        "progress_result": progress_result,
                        "syncStatus": report["syncStatus"],
                        "errorMessage": report["errorMessage"],
                    },
                })
            else:
                self.write_json({
                    "ok": False,
                    "error": "重复报工：该工人已对此工单、此工序报过工"
                }, status=HTTPStatus.CONFLICT)

        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_report_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_worker_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            worker = json.loads(body)
            if not worker.get("name", "").strip():
                self.write_json({"ok": False, "error": "工人姓名不能为空"}, status=HTTPStatus.BAD_REQUEST)
                return
            wid = worker.get("id", "").strip() or f"WK{uuid.uuid4().hex[:3].upper()}"
            name = worker["name"].strip()
            team = worker.get("team", "").strip()
            source = worker.get("source", "local")
            odoo_eid = worker.get("odooEmployeeId", 0)
            operation_codes = worker.get("operationCodes", [])
            if not isinstance(operation_codes, list):
                operation_codes = []
            unknown_ops = [code for code in operation_codes if code not in VALID_OPERATIONS]
            if unknown_ops:
                self.write_json({"ok": False, "error": "包含无效工序绑定"},
                                status=HTTPStatus.BAD_REQUEST)
                return
            existing = get_valid_worker_ids()
            if wid in existing:
                self.write_json({"ok": False, "error": f"工号 {wid} 已存在"}, status=HTTPStatus.CONFLICT)
                return
            db_add_worker(wid, name, team, source, odoo_eid, operation_codes)
            self.write_json({"ok": True, "data": {"id": wid, "name": name, "team": team,
                                                   "source": source, "odooEmployeeId": odoo_eid,
                                                   "operationCodes": operation_codes}})
        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_worker_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def dashboard_payload(self):
        try:
            return {"ok": True, "data": load_dashboard()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def report_stats_payload(self):
        try:
            today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
            reports = db_reports(date_filter=today)
            total_qty = sum(int(r.get("qty", 0)) for r in reports)
            total_hours = sum(float(r.get("hours", 0)) for r in reports)
            unique_workers = len({r.get("worker_name") for r in reports})
            mode = get_odoo_mode()
            return {
                "ok": True,
                "data": {
                    "todayCount": len(reports),
                    "todayOutput": total_qty,
                    "todayHours": round(total_hours, 1),
                    "activeWorkers": unique_workers,
                    "recentReports": [{
                        "workerName": r["worker_name"], "workerTeam": r.get("worker_team", ""),
                        "orderId": r["order_id"], "qty": r["qty"],
                        "operationLabel": r["operation_label"], "operation": r["operation"],
                        "hours": r["hours"], "time": r["time"],
                    } for r in reports[:8]],
                },
                "meta": {"mode": mode, "source": "odoo" if mode == "real" else "mock"},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def write_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # API data includes live Odoo and local report state. Prevent browsers
        # from displaying a pre-reset response while the page remains open.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# 主入口
# ============================================================

_running = True

def graceful_shutdown(signum, frame):
    global _running
    logger.info(f"收到信号 {signum}，准备关闭...")
    _running = False


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    _init_db()
    _seed_workers()
    # Mock 切 Real 时自动从 mock DB 迁移数据
    if not MOCK_MODE:
        mock_db = BASE_DIR / "data.mock.db"
        if mock_db.exists():
            try:
                with sqlite3.connect(str(mock_db)) as ms:
                    reports = ms.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
                if reports > 0:
                    logger.info(f"检测到 mock 数据库有 {reports} 条记录，可手动迁移: {mock_db}")
            except Exception:
                pass

    port = int(os.getenv("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 2
    logger.info(f"生产员工报工系统: http://0.0.0.0:{port}")
    logger.info(f"Odoo: {ODOO_URL}")
    logger.debug(f"Odoo 连接详情: db={ODOO_DB} user={ODOO_USER}")
    logger.info(f"模式: {'模拟 (MOCK)' if MOCK_MODE else '真实 (REAL)'}")
    logger.info(f"数据库: {DB_FILE}")
    logger.info(f"Auth: {'已启用' if API_KEY else '未启用（POST 接口无保护）'}")

    try:
        while _running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    logger.info("服务器已关闭")


if __name__ == "__main__":
    main()
