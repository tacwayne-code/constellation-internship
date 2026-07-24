import uuid
from datetime import datetime, date
from flask_login import UserMixin
from app import db


# ─── 辅助函数 ───
def gen_uuid():
    return str(uuid.uuid4())[:8]


def gen_doc_no():
    return 'DOC-' + datetime.now().strftime('%Y%m%d') + '-' + gen_uuid()


def gen_ecr_no():
    return 'ECR-' + datetime.now().strftime('%Y%m%d') + '-' + gen_uuid()


def gen_eco_no():
    return 'ECO-' + datetime.now().strftime('%Y%m%d') + '-' + gen_uuid()


def gen_proj_no():
    return 'PRJ-' + datetime.now().strftime('%Y%m%d') + '-' + gen_uuid()


def gen_bom_no():
    return 'BOM-' + datetime.now().strftime('%Y%m%d') + '-' + gen_uuid()


def gen_system_id():
    """系统主物料号：弱语义自动编号 ITM-YYYYMMDD-XXXXXX（不编码机型/日期/材料）"""
    return 'ITM-' + datetime.now().strftime('%Y%m%d%H%M%S') + '-' + gen_uuid()


# ──────────────── 1. 用户与权限 ────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'plm_user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user')  # admin / manager / user / viewer
    department = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)


# ──────────────── 2. 图文档案管理 ────────────────
class DocumentCategory(db.Model):
    __tablename__ = 'plm_doc_category'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('plm_doc_category.id'))
    description = db.Column(db.Text)
    children = db.relationship('DocumentCategory', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')


class Document(db.Model):
    __tablename__ = 'plm_document'
    id = db.Column(db.Integer, primary_key=True)
    doc_no = db.Column(db.String(64), unique=True, nullable=False, default=gen_doc_no)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category_id = db.Column(db.Integer, db.ForeignKey('plm_doc_category.id'))
    category = db.relationship('DocumentCategory', backref='documents')
    status = db.Column(db.String(20), default='draft')  # draft / review / approved / published / obsolete
    version = db.Column(db.String(10), default='V1.0')
    file_name = db.Column(db.String(255))
    file_path = db.Column(db.String(500))
    file_size = db.Column(db.Integer)
    tags = db.Column(db.String(500))
    author_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    author = db.relationship('User', foreign_keys=[author_id], backref='documents')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    is_locked = db.Column(db.Boolean, default=False)
    locked_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    lock_holder = db.relationship('User', foreign_keys=[locked_by])

    def new_version(self):
        v = self.version
        parts = v[1:].split('.')
        self.version = f'V{parts[0]}.{int(parts[1]) + 1}'
        self.status = 'draft'


class DocVersion(db.Model):
    __tablename__ = 'plm_doc_version'
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('plm_document.id'), nullable=False)
    document = db.relationship('Document', backref='versions')
    version = db.Column(db.String(10))
    file_name = db.Column(db.String(255))
    file_path = db.Column(db.String(500))
    file_size = db.Column(db.Integer)
    change_note = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    creator = db.relationship('User')
    created_at = db.Column(db.DateTime, default=datetime.now)


class DocApproval(db.Model):
    __tablename__ = 'plm_doc_approval'
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('plm_document.id'), nullable=False)
    document = db.relationship('Document', backref='approvals')
    step = db.Column(db.Integer, default=1)
    approver_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    approver = db.relationship('User')
    status = db.Column(db.String(20), default='pending')  # pending / approved / rejected
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, onupdate=datetime.now)


# ──────────────── 3. 流程管理 ────────────────
class Workflow(db.Model):
    __tablename__ = 'plm_workflow'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    model_type = db.Column(db.String(50))  # document / change / bom
    is_active = db.Column(db.Boolean, default=True)
    steps = db.relationship('WorkflowStep', order_by='WorkflowStep.seq', backref='workflow', lazy='dynamic')
    created_at = db.Column(db.DateTime, default=datetime.now)


class WorkflowStep(db.Model):
    __tablename__ = 'plm_workflow_step'
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('plm_workflow.id'))
    seq = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100))
    approver_role = db.Column(db.String(20))  # admin / manager
    is_parallel = db.Column(db.Boolean, default=False)


# ──────────────── 4. 结构管理 ────────────────
class Product(db.Model):
    __tablename__ = 'plm_product'
    id = db.Column(db.Integer, primary_key=True)
    # 系统主物料号（弱语义，由系统自动生成，ITM-YYYYMMDD-XXXXXX）
    system_id = db.Column(db.String(40), unique=True, nullable=True)
    # 原图号 / 显示编码（兼容历史图纸号，可重复——如通用件在不同机型中可能用同一图号）
    code = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    # 物料类型：PART/ASM/STD/BUY（与系统主物料号分离的属性）
    item_type = db.Column(db.String(20), default='PART')  # PART 零件/ASM 装配/STD 标准件/BUY 外购
    # 修订号（独立于主物料号的版本控制 R00/R01）
    revision = db.Column(db.String(10), default='R00')
    parent_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'))
    children = db.relationship('Product', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')
    level = db.Column(db.Integer, default=0)  # 0=整机, 1=部件, 2=组件, 3=零件
    status = db.Column(db.String(20), default='active')
    # 适用范围（多值关系，未来可改为关联表，暂用 JSON 风格字符串存储机型清单）
    applicable_models = db.Column(db.String(500), default='')  # 如 "710分光,910编带"
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def has_children(self):
        """是否有子产品（树状折叠用）"""
        return self.children.count() > 0

    @property
    def bom_items_count(self):
        """此产品在多少个 BOM 中作为物料被引用"""
        from app.models import BomItem
        return BomItem.query.filter_by(product_id=self.id).count()


# ──────────────── 5. BOM 管理 ────────────────
class Bom(db.Model):
    __tablename__ = 'plm_bom'
    id = db.Column(db.Integer, primary_key=True)
    bom_no = db.Column(db.String(64), unique=True, nullable=False, default=gen_bom_no)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    bom_type = db.Column(db.String(20), default='EBOM')  # EBOM / MBOM
    product_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'))
    product = db.relationship('Product', backref='boms')
    version = db.Column(db.String(10), default='V1.0')
    status = db.Column(db.String(20), default='draft')  # draft / released / obsolete
    # ── eBOM ↔ mBOM 关联 ──
    source_ebom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=True)
    source_ebom = db.relationship('Bom', remote_side=[id],
                                  backref=db.backref('derived_mboms', lazy='dynamic'),
                                  foreign_keys=[source_ebom_id])
    # ── Odoo 同步状态（仅 mBOM 使用） ──
    sync_status = db.Column(db.String(20), default='not_synced')  # not_synced / synced / sync_failed
    sync_time = db.Column(db.DateTime, nullable=True)
    sync_message = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    creator = db.relationship('User')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    items = db.relationship('BomItem', backref='bom', lazy='dynamic',
                            cascade='all, delete-orphan')
    conversions = db.relationship('BomConversion', backref='target_bom', lazy='dynamic',
                                  foreign_keys='BomConversion.target_bom_id')


class BomItem(db.Model):
    __tablename__ = 'plm_bom_item'
    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'), nullable=False)
    product = db.relationship('Product', foreign_keys=[product_id])
    # 替代料（指向另一个物料，可选）
    substitute_product_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'), nullable=True)
    substitute = db.relationship('Product', foreign_keys=[substitute_product_id])
    quantity = db.Column(db.Float, default=1.0)
    unit = db.Column(db.String(20), default='个')
    reference = db.Column(db.String(200))
    seq = db.Column(db.Integer, default=0)
    # ── 用于 mBOM 转换时标记虚拟件拆分 ──
    note = db.Column(db.String(200), nullable=True)  # 转换备注（如"虚拟件展开"、"中间半成品"）


# ──────────────── 5.1 BOM 转换记录 ────────────────
class BomConversion(db.Model):
    """记录 eBOM → mBOM 的结构转换操作"""
    __tablename__ = 'plm_bom_conversion'
    id = db.Column(db.Integer, primary_key=True)
    source_bom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=False)
    target_bom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=False)
    converted_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'), nullable=False)
    converter = db.relationship('User')
    conversion_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class BomDocument(db.Model):
    """BOM 与图文档案的多对多关联"""
    __tablename__ = 'plm_bom_document'
    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('plm_document.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    bom = db.relationship('Bom', backref=db.backref('linked_docs', lazy='dynamic'))
    document = db.relationship('Document')


class BomApproval(db.Model):
    """BOM 审批流记录"""
    __tablename__ = 'plm_bom_approval'
    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.Integer, db.ForeignKey('plm_bom.id'), nullable=False)
    step = db.Column(db.Integer, nullable=False)  # 审批步骤序号
    approver_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'), nullable=False)
    approver = db.relationship('User')
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected
    comment = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    bom = db.relationship('Bom', backref=db.backref('approvals', lazy='dynamic', order_by='BomApproval.step'))


# ──────────────── 6. 项目管理 ────────────────
class Project(db.Model):
    __tablename__ = 'plm_project'
    id = db.Column(db.Integer, primary_key=True)
    proj_no = db.Column(db.String(64), unique=True, nullable=False, default=gen_proj_no)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='planning')  # planning / active / completed / cancelled
    priority = db.Column(db.String(10), default='medium')  # high / medium / low
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    manager_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    manager = db.relationship('User', foreign_keys=[manager_id])
    created_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    creator = db.relationship('User', foreign_keys=[created_by])
    created_at = db.Column(db.DateTime, default=datetime.now)
    tasks = db.relationship('Task', backref='project', lazy='dynamic')


class Task(db.Model):
    __tablename__ = 'plm_task'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('plm_project.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    assignee_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    assignee = db.relationship('User')
    status = db.Column(db.String(20), default='todo')  # todo / in_progress / done / blocked
    priority = db.Column(db.String(10), default='medium')
    start_date = db.Column(db.Date)
    due_date = db.Column(db.Date)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)


# ──────────────── 7. 变更管理 ────────────────
class ChangeRequest(db.Model):
    __tablename__ = 'plm_change_request'
    id = db.Column(db.Integer, primary_key=True)
    ecr_no = db.Column(db.String(64), unique=True, nullable=False, default=gen_ecr_no)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    reason = db.Column(db.Text)
    priority = db.Column(db.String(10), default='medium')
    status = db.Column(db.String(20), default='draft')  # draft / submitted / analyzing / approved / rejected / closed
    applicant_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    applicant = db.relationship('User', foreign_keys=[applicant_id])
    assignee_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    assignee = db.relationship('User', foreign_keys=[assignee_id])
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    eco = db.relationship('ChangeOrder', backref='ecr', uselist=False)


class ChangeOrder(db.Model):
    __tablename__ = 'plm_change_order'
    id = db.Column(db.Integer, primary_key=True)
    eco_no = db.Column(db.String(64), unique=True, nullable=False, default=gen_eco_no)
    ecr_id = db.Column(db.Integer, db.ForeignKey('plm_change_request.id'), nullable=False)
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    impact_analysis = db.Column(db.Text)
    affected_boms = db.Column(db.Text)
    affected_docs = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')  # pending / executing / completed / cancelled
    executor_id = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    executor = db.relationship('User')
    created_at = db.Column(db.DateTime, default=datetime.now)
    completed_at = db.Column(db.DateTime)


# ──────────────── 8. 工时管理 ────────────────
class WorkHourStandard(db.Model):
    __tablename__ = 'plm_work_hour_standard'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'))
    product = db.relationship('Product', backref='work_hours')
    process_name = db.Column(db.String(100))
    standard_hours = db.Column(db.Float)
    unit = db.Column(db.String(20), default='小时')
    machine_type = db.Column(db.String(100))
    labor_type = db.Column(db.String(100))
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


# ──────────────── 9. 工艺管理 ────────────────
class ProcessRoute(db.Model):
    __tablename__ = 'plm_process_route'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('plm_product.id'))
    product = db.relationship('Product', backref='process_routes')
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='draft')
    version = db.Column(db.String(10), default='V1.0')
    created_by = db.Column(db.Integer, db.ForeignKey('plm_user.id'))
    creator = db.relationship('User')
    created_at = db.Column(db.DateTime, default=datetime.now)
    steps = db.relationship('ProcessStep', order_by='ProcessStep.seq', backref='route', lazy='dynamic')


class ProcessStep(db.Model):
    __tablename__ = 'plm_process_step'
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('plm_process_route.id'), nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(200))
    description = db.Column(db.Text)
    standard_hours = db.Column(db.Float)
    work_center = db.Column(db.String(100))
    tooling = db.Column(db.String(200))
    doc_id = db.Column(db.Integer, db.ForeignKey('plm_document.id'))
    doc = db.relationship('Document')
    created_at = db.Column(db.DateTime, default=datetime.now)


# ──────────────── 10. 集成配置 ────────────────
class IntegrationConfig(db.Model):
    __tablename__ = 'plm_integration_config'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    system_type = db.Column(db.String(30))  # odoo / cad / erp / mes
    api_url = db.Column(db.String(500))
    api_key = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=False)
    last_sync = db.Column(db.DateTime)
    sync_interval = db.Column(db.Integer, default=60)  # minutes
    created_at = db.Column(db.DateTime, default=datetime.now)


class SyncLog(db.Model):
    __tablename__ = 'plm_sync_log'
    id = db.Column(db.Integer, primary_key=True)
    integration_id = db.Column(db.Integer, db.ForeignKey('plm_integration_config.id'))
    integration = db.relationship('IntegrationConfig', backref='logs')
    direction = db.Column(db.String(10))  # import / export
    status = db.Column(db.String(20))  # success / failed
    records_count = db.Column(db.Integer, default=0)
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
