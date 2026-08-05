from flask import Flask, request, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config
import os, uuid, hmac, time, logging, hashlib

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

    # 启用 SQLite 外键约束
    with app.app_context():
        from sqlalchemy import event
        @event.listens_for(db.engine, 'connect')
        def _set_sqlite_pragma(dbapi_conn, _):
            if hasattr(dbapi_conn, 'execute'):
                dbapi_conn.execute('PRAGMA journal_mode=WAL')
                dbapi_conn.execute('PRAGMA foreign_keys=ON')

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


    # ── 登录暴力破解防护 ──
    _login_attempts = {}  # {ip_hash: (count, first_time)}

    def _login_guard():
        return _check_brute_force()
    # 将守卫函数挂到 app 上，供 login 路由调用
    app._login_guard = _login_guard

    def _check_brute_force():
        ip = request.remote_addr or 'unknown'
        h = hashlib.sha256(f'{ip}:plm_login_salt'.encode()).hexdigest()
        now = time.time()
        if h in _login_attempts:
            count, first = _login_attempts[h]
            if now - first > 300:  # 5 分钟窗口过期，重置
                _login_attempts[h] = (1, now)
                return True
            # 每 60 秒清理过期条目
            if not hasattr(_check_brute_force, '_gc') or now - _check_brute_force._gc > 60:
                _check_brute_force._gc = now
                expired = [k for k, (c, t) in list(_login_attempts.items()) if now - t > 300]
                for k in expired:
                    del _login_attempts[k]
            if count >= 10:
                abort(429, '登录失败次数过多，请等待 5 分钟后再试')
            _login_attempts[h] = (count + 1, first)
        else:
            _login_attempts[h] = (1, now)
        return True
    @app.before_request
    def csrf_protect():
        """对 POST/PUT/DELETE 请求做 CSRF token 校验"""
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        # 只豁免 boms/api (编码生成器无表单提交) 和 auth/login
        if request.path.startswith('/boms/api/'):
            return
        token = _ensure_csrf_token()
        # JSON 请求：从 Header 取 token
        if request.is_json:
            sent = request.headers.get('X-CSRFToken', '')
            if not hmac.compare_digest(sent, token):
                abort(400, 'CSRF validation failed')
            return
        # 表单请求
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

        # 匹配 4段8位新格式 XX-XX-XX-XX
        m = re.match(r'^([\d]{2}[-][\d]{2}[-][\d]{2}[-][\d]{2})(.*)', base)
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
        db.create_all()

    @app.errorhandler(400)
    def handle_csrf(e):
        if 'CSRF' in str(e):
            flash('Session expired, page is refreshing... Please try again.', 'warning')
            from flask import make_response
            html = "<html><head><meta http-equiv=\"refresh\" content=\"0;url=\"></head><body>Refreshing...</body></html>"
            resp = make_response(html)
            resp.status_code = 200
            return resp
        return abort(400, str(e))

    return app
