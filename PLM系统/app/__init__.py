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
    # SQLite WAL + 超时：并行读写不阻塞
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'timeout': 15, 'check_same_thread': False},
        'pool_size': 5,
    }

    # ── 会话安全配置 ──
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # 禁止 JS 读取 session cookie
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 防止跨站请求携带 cookie
    # SESSION_COOKIE_SECURE：仅 HTTPS 下发送 cookie；通过环境变量 PLM_SECURE_COOKIE=1 启用
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('PLM_SECURE_COOKIE') == '1'

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
        if request.path.startswith('/api/') or request.path.startswith('/boms/api/') or request.path.startswith('/integrations/') or '/push-to-odoo' in request.path:
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

    # ── 自定义 Jinja 过滤器 ──
    @app.template_filter('doc_title_parts')
    def doc_title_parts(title):
        """拆分文档标题为 (图号, 名称, 扩展名)
        例: '910-008-052球头下吸块5050.SLDDRW' → ('910-008-052', '球头下吸块', '.SLDDRW')
             'MBF-DQ04-V2'                       → ('MBF-DQ04-V2', '', '')
             '下移动台挡板.SLDDRW'                → ('', '下移动台挡板', '.SLDDRW')
             'HFSQN4-15100-375升降台右挡板'       → ('HFSQN4-15100-375', '升降台右挡板', '')
        """
        import re
        if not title:
            return ('', '', '')
        base = title
        ext = ''
        dot = base.rfind('.')
        if dot > 0 and dot > len(base) - 8:  # 扩展名通常不超过7个字符
            ext = base[dot:]
            base = base[:dot]

        # 匹配 3位机型号-3位模块号-3位流水号 开头的图号
        m = re.match(r'^([\d]{3}[-][\d]{3}[-][\d]{3})(.*)', base)
        if m:
            dwg_no = m.group(1)
            name = m.group(2).strip() if m.group(2).strip() else ''
            name = re.sub(r'\s*\d{4}$', '', name).strip()
            return (dwg_no, name, ext)

        # 匹配纯 LED 规格码开头的（如 5050球头分光机220721V3）
        m = re.match(r'^(\d{4})(.*)', base)
        if m:
            dwg_no = m.group(1)
            name = m.group(2).strip() if m.group(2).strip() else ''
            return (dwg_no, name, ext)

        # 通用拆分：以第一个汉字为界
        # 图号部分 = 第一个汉字之前的所有字符（字母+数字+-_./）
        # 名称部分 = 第一个汉字及之后
        cn = re.search(r'[\u4e00-\u9fff]', base)
        if cn:
            prefix = base[:cn.start()].rstrip(' -_.()')
            rest = base[cn.start():]
            # 智能判断：只有"明显像图号"才分离
            # 标准：长度>=5 且 含连字符/下划线分隔 且 含字母+数字
            # 简化：长度>=2 且 含分隔符（如 GY-3、SCM-60）也算
            is_dwg = (
                (len(prefix) >= 5 and re.search(r'[-_]', prefix) and re.search(r'[A-Za-z]', prefix) and re.search(r'\d', prefix))
                or (len(prefix) >= 2 and re.search(r'[-_]', prefix))
                or re.match(r'^\d{3,}$', prefix)  # 纯数字3位以上
            )
            if is_dwg:
                # 去 name 末尾的方括号代码（如 [GY-3]）
                rest = re.sub(r'\s*\[[A-Za-z0-9\-_]+\]\s*$', '', rest).strip()
                return (prefix, rest, ext)
            else:
                # 短前缀或纯数字——视为名称的一部分
                return ('', base, ext)
        else:
            # 整个没有汉字 — 视为纯图号（如 MBF-DQ04-V2、PC-CD4-XJJ-001-V2）
            return (base, '', ext)

    @app.template_filter('clean_desc')
    def clean_desc(description):
        """清洗文档描述：去掉导入路径元信息，只保留可读描述"""
        if not description:
            return ''
        # 导入时存储的格式是 "自动导入 | 路径: ... | 类型: ... | 大小: ..."
        # 如果 description 不含导入信息，原样返回
        stripped = description.strip()
        if '自动导入' in stripped or '路径:' in stripped:
            # 尝试提取路径文件名中的中文描述部分
            import re
            # 匹配路径最后一段文件名
            m = re.search(r'[^\\/]+$', stripped)
            if m:
                fname = m.group(0)
                # 去掉扩展名
                dot = fname.rfind('.')
                if dot > 0:
                    fname = fname[:dot]
                # 如果文件名是纯数字字母，返回空
                if re.match(r'^[\d\-.A-Za-z]+$', fname):
                    return ''
                return fname
            return ''
        return stripped

    with app.app_context():
        # 仅在数据库文件不存在或环境变量 PLM_AUTO_INIT=1 时执行 create_all
        # 生产环境应使用 Alembic 迁移，避免自动建表
        db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        db_exists = db_path and os.path.exists(db_path)
        if not db_exists or os.environ.get('PLM_AUTO_INIT') == '1':
            db.create_all()
        _migrate_bom_schema()
        _migrate_product_schema()
        _migrate_bom_v2()
        _migrate_document_name()

    return app


def _migrate_document_name():
    """为 plm_document 添加 name 字段（汉字名称），并从现有 title 回填（2026-07-30）"""
    import sqlite3, re
    from flask import current_app
    try:
        db_path = current_app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
        if not db_path:
            return
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info('plm_document')")
        cols = [row[1] for row in cursor.fetchall()]
        # 添加 name 字段
        if 'name' not in cols:
            cursor.execute("ALTER TABLE plm_document ADD COLUMN name VARCHAR(255) DEFAULT ''")
            print("[Migration] Added plm_document.name column")
        # 回填现有 title 的名称部分到 name 字段（仅 name 为空的才回填，避免重复工作）
        cursor.execute("SELECT id, title, name FROM plm_document WHERE name IS NULL OR name = ''")
        rows = cursor.fetchall()
        updated = 0
        for doc_id, title, _ in rows:
            # 复用与模板一致的拆分逻辑
            base = title or ''
            dot = base.rfind('.')
            if dot > 0 and dot > len(base) - 8:
                base = base[:dot]
            m = re.match(r'^([\d]{3}[-][\d]{3}[-][\d]{3})(.*)', base)
            if m:
                name = m.group(2).strip()
                name = re.sub(r'\s*\d{4}$', '', name).strip()
            else:
                m = re.match(r'^(\d{4})(.*)', base)
                if m:
                    name = m.group(2).strip()
                else:
                    # 如果标题不含汉字（即纯图号如 HFS6-3030-50、MBF-DQ04-V2）
                    # 则 name 留空（不需要把图号复制到名称字段）
                    if not re.search(r'[\u4e00-\u9fff]', base):
                        name = ''
                    else:
                        name = base
            if name:
                cursor.execute("UPDATE plm_document SET name=? WHERE id=?", (name, doc_id))
                updated += 1
        conn.commit()
        conn.close()
        if updated:
            print(f"[Migration] Backfilled {updated} document names")
    except Exception as e:
        print(f"[Migration] Document name migration failed: {e}")


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

        # ── document_id 回填：匹配 Product.code ↔ Document.title ──
        if 'document_id' not in existing:
            cursor.execute("ALTER TABLE plm_product ADD COLUMN document_id INTEGER REFERENCES plm_document(id)")
        # 回填：通过图号匹配
        cursor.execute("""
            UPDATE plm_product SET document_id = (
                SELECT d.id FROM plm_document d
                WHERE d.title = plm_product.code
                LIMIT 1
            )
            WHERE plm_product.document_id IS NULL
        """)
        matched = cursor.rowcount if cursor.rowcount else 0

        # ── 已绑定的产品：不再自动覆盖 Product.code（保持原值，避免破坏历史编码） ──

        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_plm_product_system_id ON plm_product(system_id)")
        except Exception:
            pass

        conn.commit()
        conn.close()
        if alter_sqls or rows:
            print(f"[Migration] Product: {len(alter_sqls)} schema + {len(rows)} system_ids + {matched} document_ids backfilled")
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

        # 编码体系迁移（2026-07-29）
        cursor.execute("PRAGMA table_info('plm_bom_item')")
        item_cols = [row[1] for row in cursor.fetchall()]
        if 'code' not in item_cols:
            alter_sqls.append("ALTER TABLE plm_bom_item ADD COLUMN code VARCHAR(64) DEFAULT ''")
        if 'part_type' not in item_cols:
            alter_sqls.append("ALTER TABLE plm_bom_item ADD COLUMN part_type VARCHAR(16) DEFAULT ''")

        for sql in alter_sqls:
            cursor.execute(sql)
        conn.commit()
        conn.close()
        if alter_sqls:
            print(f"[Migration] Applied {len(alter_sqls)} schema changes for BOM module")
    except Exception as e:
        print(f"[Migration] Schema migration skipped or failed: {e}")
