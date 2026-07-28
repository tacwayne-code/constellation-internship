from flask import Flask, request, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config
import os, uuid, hmac

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes import auth_bp, document_bp, workflow_bp, structure_bp, \
        bom_bp, project_bp, change_bp, workhour_bp, process_bp, integration_bp, main_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(main_bp)
    app.register_blueprint(document_bp, url_prefix='/documents')
    app.register_blueprint(workflow_bp, url_prefix='/workflows')
    app.register_blueprint(structure_bp, url_prefix='/structures')
    app.register_blueprint(bom_bp, url_prefix='/boms')
    app.register_blueprint(project_bp, url_prefix='/projects')
    app.register_blueprint(change_bp, url_prefix='/changes')
    app.register_blueprint(workhour_bp, url_prefix='/work-hours')
    app.register_blueprint(process_bp, url_prefix='/processes')
    app.register_blueprint(integration_bp, url_prefix='/integrations')

    # ── CSRF 防护 ──
    def _ensure_csrf_token():
        """确保 session 中有 csrf_token，无则创建"""
        if 'csrf_token' not in session:
            session['csrf_token'] = uuid.uuid4().hex
        return session['csrf_token']

    @app.before_request
    def csrf_protect():
        """对 POST/PUT/DELETE 请求做简易 CSRF token 校验"""
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        if request.path.startswith('/api/') or request.path.startswith('/integrations/') or '/push-to-odoo' in request.path:
            return
        token = _ensure_csrf_token()
        if request.form:
            sent = request.form.get('csrf_token', '')
            if not sent and request.path in ('/auth/login',):
                return
            if not hmac.compare_digest(sent, token):
                abort(400, 'CSRF validation failed')

    @app.context_processor
    def inject_csrf():
        """向所有模板注入 csrf_token"""
        return {'csrf_token': _ensure_csrf_token()}

    with app.app_context():
        db.create_all()
        _migrate_bom_schema()
        _migrate_product_schema()
        _migrate_bom_v2()

    return app


def _migrate_bom_v2():
    """BOM v2 迁移：BomDocument 关联表、BomApproval 审批表、BomItem 替代料"""
    import sqlite3
    from flask import current_app
    try:
        db_path = current_app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path:
            return
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # plm_bom_item 加 substitute_product_id
        cursor.execute("PRAGMA table_info('plm_bom_item')")
        cols = [r[1] for r in cursor.fetchall()]
        if 'substitute_product_id' not in cols:
            cursor.execute("ALTER TABLE plm_bom_item ADD COLUMN substitute_product_id INTEGER")
            print("[Migration] BomItem: added substitute_product_id")

        # plm_bom_document 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plm_bom_document'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE plm_bom_document (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    created_at TIMESTAMP,
                    FOREIGN KEY(bom_id) REFERENCES plm_bom(id),
                    FOREIGN KEY(document_id) REFERENCES plm_document(id)
                )
            """)
            print("[Migration] Created plm_bom_document table")

        # plm_bom_approval 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plm_bom_approval'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE plm_bom_approval (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER NOT NULL,
                    step INTEGER NOT NULL,
                    approver_id INTEGER NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    comment TEXT,
                    decided_at TIMESTAMP,
                    created_at TIMESTAMP,
                    FOREIGN KEY(bom_id) REFERENCES plm_bom(id),
                    FOREIGN KEY(approver_id) REFERENCES plm_user(id)
                )
            """)
            print("[Migration] Created plm_bom_approval table")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Migration] BOM v2 skipped: {e}")


def _migrate_product_schema():
    """为 plm_product 表补充编码体系字段（系统主物料号/类型/修订/适用范围）"""
    import sqlite3, uuid
    from datetime import datetime
    from flask import current_app
    try:
        db_path = current_app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path:
            return
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info('plm_product')")
        existing = [row[1] for row in cursor.fetchall()]
        alter_sqls = []
        if 'system_id' not in existing:
            alter_sqls.append("ALTER TABLE plm_product ADD COLUMN system_id VARCHAR(40)")
        if 'item_type' not in existing:
            alter_sqls.append("ALTER TABLE plm_product ADD COLUMN item_type VARCHAR(20) DEFAULT 'PART'")
        if 'revision' not in existing:
            alter_sqls.append("ALTER TABLE plm_product ADD COLUMN revision VARCHAR(10) DEFAULT 'R00'")
        if 'applicable_models' not in existing:
            alter_sqls.append("ALTER TABLE plm_product ADD COLUMN applicable_models VARCHAR(500) DEFAULT ''")

        for sql in alter_sqls:
            cursor.execute(sql)

        # Backfill system_id for existing products
        cursor.execute("SELECT id FROM plm_product WHERE system_id IS NULL OR system_id = ''")
        rows = cursor.fetchall()
        for (pid,) in rows:
            new_sid = f"ITM-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"
            try:
                cursor.execute("UPDATE plm_product SET system_id = ? WHERE id = ?", (new_sid, pid))
            except Exception:
                pass

        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_plm_product_system_id ON plm_product(system_id)")
        except Exception:
            pass

        conn.commit()
        conn.close()
        if alter_sqls or rows:
            print(f"[Migration] Product: {len(alter_sqls)} schema + {len(rows)} system_ids backfilled")
    except Exception as e:
        print(f"[Migration] Product migration skipped: {e}")


def _migrate_bom_schema():
    """兼容旧数据库：为 plm_bom 表补充新增字段"""
    import sqlite3
    from flask import current_app
    try:
        db_path = current_app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path:
            return
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # 检查已有字段
        cursor.execute("PRAGMA table_info('plm_bom')")
        existing = [row[1] for row in cursor.fetchall()]
        alter_sqls = []
        if 'source_ebom_id' not in existing:
            alter_sqls.append("ALTER TABLE plm_bom ADD COLUMN source_ebom_id INTEGER")
        if 'sync_status' not in existing:
            alter_sqls.append("ALTER TABLE plm_bom ADD COLUMN sync_status VARCHAR(20) DEFAULT 'not_synced'")
        if 'sync_time' not in existing:
            alter_sqls.append("ALTER TABLE plm_bom ADD COLUMN sync_time DATETIME")
        if 'sync_message' not in existing:
            alter_sqls.append("ALTER TABLE plm_bom ADD COLUMN sync_message TEXT")
        if 'note' not in existing:
            cursor.execute("PRAGMA table_info('plm_bom_item')")
            item_existing = [row[1] for row in cursor.fetchall()]
            if 'note' not in item_existing:
                alter_sqls.append("ALTER TABLE plm_bom_item ADD COLUMN note VARCHAR(200)")

        for sql in alter_sqls:
            cursor.execute(sql)
        conn.commit()
        conn.close()
        if alter_sqls:
            print(f"[Migration] Applied {len(alter_sqls)} schema changes for BOM module")
    except Exception as e:
        print(f"[Migration] Schema migration skipped or failed: {e}")
