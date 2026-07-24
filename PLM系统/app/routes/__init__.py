import os, uuid
from datetime import datetime, date
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from app.models import *

# ─── Blueprints ───
auth_bp = Blueprint('auth', __name__)
main_bp = Blueprint('main', __name__)
document_bp = Blueprint('documents', __name__)
workflow_bp = Blueprint('workflows', __name__)
structure_bp = Blueprint('structures', __name__)
bom_bp = Blueprint('boms', __name__)
project_bp = Blueprint('projects', __name__)
change_bp = Blueprint('changes', __name__)
workhour_bp = Blueprint('work_hours', __name__)
process_bp = Blueprint('processes', __name__)
integration_bp = Blueprint('integrations', __name__)


# ─── 辅助函数 ───
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in \
        {'pdf', 'dwg', 'dxf', 'step', 'stp', 'igs', 'prt', 'asm', 'sldprt', 'sldasm',
         'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'zip', 'rar', 'jpg', 'png'}


def save_file(file):
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        folder = current_app.config['UPLOAD_FOLDER']
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, fname)
        file.save(path)
        return fname, file.filename, os.path.getsize(path)
    return None, None, 0


# ════════════════════════════════
#  AUTH
# ════════════════════════════════
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']) and user.is_active:
            login_user(user)
            return redirect(url_for('main.dashboard'))
        flash('用户名或密码错误', 'danger')
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


# ════════════════════════════════
#  MAIN / DASHBOARD
# ════════════════════════════════
@main_bp.route('/')
@login_required
def dashboard():
    doc_count = Document.query.count()
    active_projects = Project.query.filter_by(status='active').count()
    pending_changes = ChangeRequest.query.filter(
        ChangeRequest.status.in_(['submitted', 'analyzing'])).count()

    # 待我审批的
    my_pending_approvals = DocApproval.query.filter_by(
        approver_id=current_user.id, status='pending').all()
    pending_approvals = len(my_pending_approvals)

    # 我的待办变更
    my_pending_changes = ChangeRequest.query.filter(
        ChangeRequest.assignee_id == current_user.id,
        ChangeRequest.status.in_(['submitted', 'analyzing'])).limit(5).all()

    recent_docs = Document.query.order_by(Document.updated_at.desc()).limit(5).all()
    recent_changes = ChangeRequest.query.order_by(ChangeRequest.created_at.desc()).limit(5).all()

    # BOM 统计
    ebom_count = Bom.query.filter_by(bom_type='EBOM').count()
    mbom_count = Bom.query.filter_by(bom_type='MBOM').count()
    # 待推送的已发布 mBOM
    pending_sync_mboms = Bom.query.filter_by(
        bom_type='MBOM', status='released', sync_status='not_synced'
    ).count()

    return render_template('dashboard.html', doc_count=doc_count, pending_approvals=pending_approvals,
                           active_projects=active_projects, pending_changes=pending_changes,
                           my_pending_approvals=my_pending_approvals,
                           my_pending_changes=my_pending_changes,
                           recent_docs=recent_docs, recent_changes=recent_changes,
                           ebom_count=ebom_count, mbom_count=mbom_count,
                           pending_sync_mboms=pending_sync_mboms)


# ════════════════════════════════
#  1. 图文档案管理
# ════════════════════════════════
@document_bp.route('/')
@login_required
def list_docs():
    category_id = request.args.get('category_id', type=int)
    keyword = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '')
    fmt_filter = request.args.get('fmt', '').strip()  # PDF/CAD/3D 格式筛选
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = Document.query
    if category_id:
        query = query.filter_by(category_id=category_id)
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(
            Document.title.ilike(like),
            Document.doc_no.ilike(like),
            Document.tags.ilike(like),
            Document.description.ilike(like)
        ))
    if status_filter:
        query = query.filter_by(status=status_filter)
    if fmt_filter:
        query = query.filter(Document.file_name.ilike(f'%.{fmt_filter}'))

    # 分页
    total = query.count()
    docs = query.order_by(Document.updated_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    categories = DocumentCategory.query.all()
    from sqlalchemy import func
    cat_rows = db.session.query(Document.category_id, func.count(Document.id)).group_by(Document.category_id).all()
    category_counts = {cid: cnt for cid, cnt in cat_rows if cid is not None}
    for cat in categories:
        if cat.id not in category_counts:
            category_counts[cat.id] = 0

    # 当前分类下各格式的文档数（用于子筛选 tab）
    fmt_counts = {}
    if category_id:
        # 用 LIKE 匹配完整扩展名（处理 .STEP .SLDDRW 这种长度 > 3 的）
        exts_to_count = ['pdf', 'dwg', 'drw', 'dxf', 'slddrw', 'sldprt', 'sldasm', 'step', 'stp', 'xt', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar', 'jpg', 'png']
        for ext in exts_to_count:
            cnt = Document.query.filter(
                Document.category_id == category_id,
                Document.file_name.ilike(f'%.{ext}')
            ).count()
            if cnt > 0:
                fmt_counts[ext] = cnt

    total_pages = (total + per_page - 1) // per_page
    return render_template('documents/list.html', docs=docs, categories=categories,
                           category_counts=category_counts,
                           keyword=keyword, status_filter=status_filter,
                           fmt_filter=fmt_filter, fmt_counts=fmt_counts,
                           page=page, per_page=per_page, total=total, total_pages=total_pages,
                           category_id=category_id)


@document_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_doc():
    if request.method == 'POST':
        doc = Document(
            title=request.form['title'],
            description=request.form.get('description', ''),
            category_id=request.form.get('category_id', type=int),
            tags=request.form.get('tags', ''),
            author_id=current_user.id
        )
        file = request.files.get('file')
        if file and file.filename:
            fname, orig_name, fsize = save_file(file)
            if fname:
                doc.file_name = orig_name
                doc.file_path = fname
                doc.file_size = fsize
        db.session.add(doc)
        db.session.commit()
        flash('文档创建成功', 'success')
        return redirect(url_for('documents.list_docs'))
    categories = DocumentCategory.query.all()
    return render_template('documents/create.html', categories=categories)


@document_bp.route('/<int:id>')
@login_required
def view_doc(id):
    doc = Document.query.get_or_404(id)
    return render_template('documents/view.html', doc=doc)


@document_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_doc(id):
    doc = Document.query.get_or_404(id)
    if request.method == 'POST':
        doc.title = request.form['title']
        doc.description = request.form.get('description', '')
        doc.category_id = request.form.get('category_id', type=int)
        doc.tags = request.form.get('tags', '')
        file = request.files.get('file')
        if file and file.filename:
            fname, orig_name, fsize = save_file(file)
            if fname:
                doc.file_name = orig_name
                doc.file_path = fname
                doc.file_size = fsize
        db.session.commit()
        flash('文档更新成功', 'success')
        return redirect(url_for('documents.view_doc', id=id))
    categories = DocumentCategory.query.all()
    return render_template('documents/edit.html', doc=doc, categories=categories)


@document_bp.route('/<int:id>/submit-approval', methods=['POST'])
@login_required
def submit_approval(id):
    doc = Document.query.get_or_404(id)
    # 防止重复提交审批
    if doc.status in ('review', 'approved', 'published'):
        flash('该文档已在审批流程中或已审批完成', 'warning')
        return redirect(url_for('documents.view_doc', id=id))
    # 关键修复：先清掉该文档的所有历史审批记录（防止驳回重提时出现重复记录）
    DocApproval.query.filter_by(document_id=id).delete()
    doc.status = 'review'
    # 创建审批步骤：每个经理审批一次（按 step 顺序）
    managers = User.query.filter_by(role='manager').all()
    for i, m in enumerate(managers):
        appr = DocApproval(document_id=id, step=i + 1, approver_id=m.id)
        db.session.add(appr)
    db.session.commit()
    flash('已提交审批', 'success')
    return redirect(url_for('documents.view_doc', id=id))


@document_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_doc(id):
    doc = Document.query.get_or_404(id)
    # 只有作者本人或管理员可删除
    if doc.author_id != current_user.id and current_user.role != 'admin':
        flash('只有创建者或管理员才能删除文档', 'danger')
        return redirect(url_for('documents.view_doc', id=id))
    # 已发布文档需特殊处理
    if doc.status == 'published':
        flash('已发布的文档不能直接删除，请先作废', 'warning')
        return redirect(url_for('documents.view_doc', id=id))
    # 删除关联文件
    if doc.file_path:
        try:
            import os as _os
            fpath = _os.path.join(current_app.config['UPLOAD_FOLDER'], doc.file_path)
            if _os.path.exists(fpath):
                _os.remove(fpath)
        except Exception:
            pass
    # 删除关联的版本和审批记录
    DocVersion.query.filter_by(document_id=id).delete()
    DocApproval.query.filter_by(document_id=id).delete()
    db.session.delete(doc)
    db.session.commit()
    flash(f'文档「{doc.title}」已删除', 'success')
    return redirect(url_for('documents.list_docs'))


@document_bp.route('/<int:id>/approve', methods=['POST'])
@login_required
def approve_doc(id):
    approval = DocApproval.query.filter_by(document_id=id, approver_id=current_user.id, status='pending').first()
    if approval:
        action = request.form.get('action', 'approved')
        approval.status = action
        approval.updated_at = datetime.now()
        approval.comment = request.form.get('comment', '')
        if action == 'approved':
            next_pending = DocApproval.query.filter_by(document_id=id, status='pending').first()
            if not next_pending:
                doc = Document.query.get(id)
                doc.status = 'approved'
        else:
            doc = Document.query.get(id)
            doc.status = 'draft'
        db.session.commit()
        flash(f'审批完成：{action}', 'success')
    return redirect(url_for('documents.view_doc', id=id))


@document_bp.route('/<int:id>/publish', methods=['POST'])
@login_required
def publish_doc(id):
    doc = Document.query.get_or_404(id)
    doc.status = 'published'
    db.session.commit()
    flash('文档已发布', 'success')
    return redirect(url_for('documents.view_doc', id=id))


@document_bp.route('/<int:id>/new-version', methods=['GET', 'POST'])
@login_required
def new_version(id):
    doc = Document.query.get_or_404(id)
    if request.method == 'POST':
        old_version = doc.version
        # Save current as version history
        dv = DocVersion(
            document_id=id, version=old_version,
            file_name=doc.file_name, file_path=doc.file_path,
            file_size=doc.file_size,
            change_note=request.form.get('change_note', ''),
            created_by=current_user.id
        )
        db.session.add(dv)
        # Create new version
        doc.new_version()
        file = request.files.get('file')
        if file and file.filename:
            fname, orig_name, fsize = save_file(file)
            if fname:
                doc.file_name = orig_name
                doc.file_path = fname
                doc.file_size = fsize
        db.session.commit()
        flash('新版本已创建', 'success')
        return redirect(url_for('documents.view_doc', id=id))
    return render_template('documents/new_version.html', doc=doc)


@document_bp.route('/<int:id>/lock')
@login_required
def lock_doc(id):
    doc = Document.query.get_or_404(id)
    if doc.is_locked and doc.locked_by != current_user.id:
        flash('文档已被其他人锁定', 'warning')
    else:
        doc.is_locked = True
        doc.locked_by = current_user.id
        db.session.commit()
        flash('文档已锁定', 'success')
    return redirect(url_for('documents.view_doc', id=id))


@document_bp.route('/<int:id>/unlock')
@login_required
def unlock_doc(id):
    doc = Document.query.get_or_404(id)
    if doc.locked_by == current_user.id or current_user.role == 'admin':
        doc.is_locked = False
        doc.locked_by = None
        db.session.commit()
        flash('文档已解锁', 'success')
    else:
        flash('只有锁定者或管理员可以解锁', 'warning')
    return redirect(url_for('documents.view_doc', id=id))


# ─── 文档分类管理 ───
@document_bp.route('/categories')
@login_required
def list_categories():
    cats = DocumentCategory.query.all()
    return render_template('documents/categories.html', cats=cats)


@document_bp.route('/categories/create', methods=['POST'])
@login_required
def create_category():
    cat = DocumentCategory(
        name=request.form['name'],
        parent_id=request.form.get('parent_id', type=int),
        description=request.form.get('description', '')
    )
    db.session.add(cat)
    db.session.commit()
    flash('分类创建成功', 'success')
    return redirect(url_for('documents.list_categories'))


# ════════════════════════════════
#  2. 流程管理
# ════════════════════════════════
@workflow_bp.route('/')
@login_required
def list_workflows():
    workflows = Workflow.query.order_by(Workflow.created_at.desc()).all()
    return render_template('workflows/list.html', workflows=workflows)


@workflow_bp.route('/<int:id>')
@login_required
def view_workflow(id):
    wf = Workflow.query.get_or_404(id)
    return render_template('workflows/view.html', workflow=wf)


@workflow_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_workflow(id):
    wf = Workflow.query.get_or_404(id)
    if request.method == 'POST':
        wf.name = request.form['name']
        wf.description = request.form.get('description', '')
        wf.model_type = request.form.get('model_type', 'document')
        wf.is_active = request.form.get('is_active') == 'on'
        # 重置步骤
        WorkflowStep.query.filter_by(workflow_id=wf.id).delete()
        step_names = request.form.getlist('step_name[]')
        step_roles = request.form.getlist('step_role[]')
        for i, (sn, sr) in enumerate(zip(step_names, step_roles)):
            if sn.strip():
                step = WorkflowStep(workflow_id=wf.id, seq=i + 1, name=sn, approver_role=sr)
                db.session.add(step)
        db.session.commit()
        flash('流程已更新', 'success')
        return redirect(url_for('workflows.list_workflows'))
    return render_template('workflows/create.html', workflow=wf, edit_mode=True)


@workflow_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_workflow(id):
    wf = Workflow.query.get_or_404(id)
    WorkflowStep.query.filter_by(workflow_id=wf.id).delete()
    db.session.delete(wf)
    db.session.commit()
    flash(f'流程「{wf.name}」已删除', 'success')
    return redirect(url_for('workflows.list_workflows'))


@workflow_bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
def toggle_workflow(id):
    wf = Workflow.query.get_or_404(id)
    wf.is_active = not wf.is_active
    db.session.commit()
    flash(f'流程「{wf.name}」已{"启用" if wf.is_active else "禁用"}', 'success')
    return redirect(url_for('workflows.list_workflows'))


@workflow_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_workflow():
    if request.method == 'POST':
        wf = Workflow(
            name=request.form['name'],
            description=request.form.get('description', ''),
            model_type=request.form.get('model_type', 'document')
        )
        db.session.add(wf)
        db.session.flush()
        step_names = request.form.getlist('step_name[]')
        step_roles = request.form.getlist('step_role[]')
        for i, (sn, sr) in enumerate(zip(step_names, step_roles)):
            if sn.strip():
                step = WorkflowStep(workflow_id=wf.id, seq=i + 1, name=sn, approver_role=sr)
                db.session.add(step)
        db.session.commit()
        flash('流程创建成功', 'success')
        return redirect(url_for('workflows.list_workflows'))
    return render_template('workflows/create.html')


# ════════════════════════════════
#  3. 结构管理
# ════════════════════════════════
@structure_bp.route('/')
@login_required
def tree():
    keyword = request.args.get('q', '').strip()
    level_filter = request.args.get('level', type=int)
    query = Product.query
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(
            Product.code.ilike(like),
            Product.name.ilike(like),
            Product.description.ilike(like)
        ))
    if level_filter is not None:
        query = query.filter_by(level=level_filter)
    # 默认显示根产品
    products = query.filter_by(parent_id=None).all()
    # 统计各级产品数量
    level_counts = {
        0: Product.query.filter_by(level=0).count(),
        1: Product.query.filter_by(level=1).count(),
        2: Product.query.filter_by(level=2).count(),
        3: Product.query.filter_by(level=3).count(),
    }
    return render_template('structures/tree.html', products=products,
                           keyword=keyword, level_filter=level_filter,
                           level_counts=level_counts)


@structure_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_product():
    if request.method == 'POST':
        # 系统主物料号（唯一标识）
        new_sid = gen_system_id()
        while Product.query.filter_by(system_id=new_sid).first():
            new_sid = gen_system_id()
        p = Product(
            system_id=new_sid,
            code=new_sid,  # code 跟随 system_id，不再单独维护原图号
            name=request.form['name'],
            description=request.form.get('description', ''),
            item_type=request.form.get('item_type', 'PART'),
            revision=request.form.get('revision', 'R00'),
            parent_id=request.form.get('parent_id', type=int),
            level=int(request.form.get('level', 0)),
            applicable_models=request.form.get('applicable_models', '')
        )
        db.session.add(p)
        db.session.commit()
        flash(f'产品创建成功！主物料号：{p.system_id}', 'success')
        return redirect(url_for('structures.product_detail', id=p.id))
    parents = Product.query.all()
    return render_template('structures/create.html', parents=parents)


@structure_bp.route('/<int:id>/detail')
@login_required
def product_detail(id):
    """产品详情页：显示基本信息、关联 BOM、关联文档、父子产品"""
    p = Product.query.get_or_404(id)
    # 关联的 eBOM
    boms = Bom.query.filter_by(product_id=id, bom_type='EBOM').all()
    # 父产品
    parent = Product.query.get(p.parent_id) if p.parent_id else None
    # 子产品
    children = p.children.all()
    # 反向引用：哪些 BOM 用此产品作为物料
    used_in_boms = db.session.query(Bom).join(BomItem).filter(BomItem.product_id == id).all()
    # 关联文档
    docs = Document.query.filter(Document.description.contains(p.code)).limit(20).all()
    return render_template('structures/detail.html', product=p,
                           boms=boms, parent=parent, children=children,
                           used_in_boms=used_in_boms, docs=docs)


@structure_bp.route('/<int:id>/children')
@login_required
def get_children(id):
    p = Product.query.get_or_404(id)
    children = [{'id': c.id, 'code': c.code, 'name': c.name, 'level': c.level,
                 'has_children': c.children.count() > 0} for c in p.children]
    return jsonify(children)


@structure_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_product(id):
    p = Product.query.get_or_404(id)
    if p.children.count() > 0:
        flash(f'「{p.name}」下还有 {p.children.count()} 个子产品，请先删除子产品', 'warning')
        return redirect(url_for('structures.tree'))
    # 检查是否被BOM引用
    if p.boms.count() > 0:
        flash(f'「{p.name}」已被 BOM 引用，不能删除', 'warning')
        return redirect(url_for('structures.tree'))
    db.session.delete(p)
    db.session.commit()
    flash(f'产品「{p.name}」已删除', 'success')
    return redirect(url_for('structures.tree'))


# ════════════════════════════════
#  4. BOM 管理 — eBOM 为源，mBOM 由 eBOM 结构转换生成
# ════════════════════════════════
@bom_bp.route('/')
@login_required
def list_boms():
    bom_type = request.args.get('type', '')  # EBOM / MBOM / all
    sync_filter = request.args.get('sync', '')  # not_synced / synced
    query = Bom.query
    if bom_type:
        query = query.filter_by(bom_type=bom_type)
    if sync_filter:
        query = query.filter_by(sync_status=sync_filter)
    boms = query.order_by(Bom.updated_at.desc()).all()
    # 统计数据
    ebom_count = Bom.query.filter_by(bom_type='EBOM').count()
    mbom_count = Bom.query.filter_by(bom_type='MBOM').count()
    pending_sync = Bom.query.filter_by(bom_type='MBOM', sync_status='not_synced', status='released').count()
    return render_template('boms/list.html', boms=boms,
                           bom_type=bom_type, sync_filter=sync_filter,
                           ebom_count=ebom_count, mbom_count=mbom_count,
                           pending_sync=pending_sync)


@bom_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_bom():
    """创建 BOM — 默认为 eBOM（设计BOM），是产品数据的唯一源头"""
    if request.method == 'POST':
        bom_type = request.form.get('bom_type', 'EBOM')
        bom = Bom(
            name=request.form['name'],
            description=request.form.get('description', ''),
            bom_type=bom_type,
            product_id=request.form.get('product_id', type=int),
            created_by=current_user.id
        )
        db.session.add(bom)
        db.session.flush()
        prod_ids = request.form.getlist('product_id[]')
        qtys = request.form.getlist('qty[]')
        units = request.form.getlist('unit[]')
        notes = request.form.getlist('note[]')
        for i, (pid, qty, unit) in enumerate(zip(prod_ids, qtys, units)):
            if pid and qty:
                note = notes[i] if i < len(notes) else ''
                bi = BomItem(bom_id=bom.id, product_id=int(pid),
                             quantity=float(qty), unit=unit, seq=i + 1, note=note)
                db.session.add(bi)
        db.session.commit()
        type_label = 'eBOM（工程 BOM）' if bom_type == 'EBOM' else 'mBOM（制造 BOM）'
        flash(f'{type_label} 创建成功', 'success')
        return redirect(url_for('boms.list_boms'))
    products = Product.query.all()
    return render_template('boms/create.html', products=products)


# ── Excel 导入 eBOM ──
@bom_bp.route('/import-excel', methods=['POST'])
@login_required
def import_excel_bom():
    """上传 Excel 文件，解析其中物料自动创建 eBOM 和关联产品"""
    file = request.files.get('excel_file')
    if not file or not file.filename:
        flash('请选择一个 Excel 文件', 'warning')
        return redirect(url_for('boms.create_bom'))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('xls', 'xlsx'):
        flash('仅支持 .xls 或 .xlsx 格式', 'warning')
        return redirect(url_for('boms.create_bom'))

    import tempfile, os
    tmp_path = os.path.join(tempfile.gettempdir(), f'bom_import_{os.urandom(4).hex()}.{ext}')
    file.save(tmp_path)

    try:
        # 解析 Excel
        items = _parse_excel_bom(tmp_path)
        if not items:
            flash('Excel 中没有识别到有效的物料数据。请确保表格有"序号""名称""规格型号""用量"列。', 'warning')
            return redirect(url_for('boms.create_bom'))

        # 创建 eBOM
        bom_name = request.form.get('name', file.filename.rsplit('.', 1)[0])
        bom_type = request.form.get('bom_type', 'EBOM')
        product_id = request.form.get('product_id', type=int)

        bom = Bom(
            name=bom_name,
            description=f'从 Excel 导入：{file.filename}',
            bom_type=bom_type,
            product_id=product_id,
            created_by=current_user.id,
            status='draft'
        )
        db.session.add(bom)
        db.session.flush()

        # 合并同一规格型号的物料行
        merged = {}
        for item in items:
            key = item['spec'] if item['spec'] else item['name']
            if key in merged:
                merged[key]['usage'] += item['usage']
            else:
                merged[key] = item

        item_count = 0
        new_product_count = 0
        for seq, (key, item) in enumerate(merged.items(), 1):
            spec = item['spec'] if item['spec'] else item['name']
            # 新建或复用产品
            existing = Product.query.filter_by(code=spec).first()
            if not existing:
                existing = Product(
                    code=spec[:80], name=item['name'][:200],
                    level=3, status='active'
                )
                db.session.add(existing)
                db.session.flush()
                new_product_count += 1

            bi = BomItem(
                bom_id=bom.id, product_id=existing.id,
                quantity=item['usage'], unit=item['unit'],
                seq=seq,
                note=f"{item['brand']} | {item['supplier']} | {item['note']}"[:200]
            )
            db.session.add(bi)
            item_count += 1

        db.session.commit()
        flash(f'导入成功！创建 eBOM「{bom_name}」共 {item_count} 项物料，自动创建 {new_product_count} 个新产品。', 'success')
        return redirect(url_for('boms.view_bom', id=bom.id))

    except Exception as e:
        flash(f'导入失败：{str(e)}', 'danger')
        return redirect(url_for('boms.create_bom'))
    finally:
        try:
            os.remove(tmp_path)
        except:
            pass


def _parse_excel_bom(filepath):
    """解析 Excel BOM，返回物料列表（同 import_mechanical.py 的 parse_excel_bom）"""
    ext = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else ''

    if ext == 'xls':
        import xlrd
        wb = xlrd.open_workbook(filepath)
        sheets = [(sn, wb.sheet_by_name(sn)) for sn in wb.sheet_names()]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, data_only=True)
        sheets = [(sn, wb[sn]) for sn in wb.sheetnames]

    items = []
    for sn, sh in sheets:
        if ext == 'xls':
            nrows, ncols = sh.nrows, sh.ncols
            cell = lambda r, c: str(sh.cell_value(r, c)).strip()
        else:
            nrows, ncols = sh.max_row + 1, sh.max_column + 1
            cell = lambda r, c: str(sh.cell_value(r, c) or '').strip()

        # 找表头行
        header_row = None
        header = []
        for r in range(min(nrows, 30)):
            row_vals = [cell(r, c) for c in range(min(ncols, 16))]
            if any('序号' in v for v in row_vals):
                header_row = r
                header = row_vals
                break
        if header_row is None:
            continue

        col_map = {}
        for i, h in enumerate(header):
            if '序号' in h: col_map['seq'] = i
            elif '名称' in h: col_map['name'] = i
            elif '规格型号' in h or '型号' in h: col_map['spec'] = i
            elif '品牌' in h: col_map['brand'] = i
            elif '用量' in h: col_map['usage'] = i
            elif '数量' in h and '用量' not in h: col_map['qty'] = i
            elif '单位' in h: col_map['unit'] = i
            elif '供应商' in h: col_map['supplier'] = i
            elif '备注' in h: col_map['note'] = i

        if 'name' not in col_map and 'spec' not in col_map:
            continue

        for r in range(header_row + 1, nrows):
            seq_val = cell(r, col_map.get('seq', 0))
            name_val = cell(r, col_map.get('name', 1))
            spec_val = cell(r, col_map.get('spec', 2))

            if not seq_val or seq_val == '0.0':
                continue

            try:
                float(seq_val)
            except ValueError:
                continue

            if not name_val and not spec_val:
                continue

            usage = 1.0
            if 'usage' in col_map:
                try:
                    usage = float(cell(r, col_map['usage']))
                except (ValueError, TypeError):
                    usage = 1.0
            elif 'qty' in col_map:
                try:
                    usage = float(cell(r, col_map['qty']))
                except (ValueError, TypeError):
                    usage = 1.0

            unit = cell(r, col_map['unit']) if 'unit' in col_map else '个'
            if not unit or unit == '':
                unit = '个'

            items.append({
                'name': name_val,
                'spec': spec_val,
                'brand': cell(r, col_map['brand']) if 'brand' in col_map else '',
                'usage': usage,
                'unit': unit,
                'supplier': cell(r, col_map['supplier']) if 'supplier' in col_map else '',
                'note': cell(r, col_map['note']) if 'note' in col_map else '',
            })

    return items


@bom_bp.route('/<int:id>')
@login_required
def view_bom(id):
    bom = Bom.query.get_or_404(id)
    products = Product.query.all()
    # 获取该 eBOM 派生的 mBOM 列表
    derived_mboms = []
    # 关联文档
    linked_docs = bom.linked_docs.all() if hasattr(bom, 'linked_docs') else []
    # 审批记录
    approvals = bom.approvals.all() if hasattr(bom, 'approvals') else []
    # 同系列版本（用于版本对比）
    sibling_boms = Bom.query.filter(
        Bom.id != bom.id,
        Bom.product_id == bom.product_id,
        Bom.bom_type == bom.bom_type
    ).order_by(Bom.version.desc()).limit(20).all()
    # 全局文档/产品列表（Tab 选择用）
    all_docs = Document.query.order_by(Document.title).limit(200).all()
    all_products = Product.query.order_by(Product.name).limit(500).all()

    # 组件分页
    items_page = request.args.get('items_page', 1, type=int)
    items_per_page = 20
    items_total = bom.items.count()
    items_total_pages = max(1, (items_total + items_per_page - 1) // items_per_page)
    items = bom.items.order_by(BomItem.seq).offset((items_page - 1) * items_per_page).limit(items_per_page).all()

    return render_template('boms/view.html', bom=bom, products=products,
                           derived_mboms=derived_mboms, linked_docs=linked_docs,
                           approvals=approvals, sibling_boms=sibling_boms,
                           all_docs=all_docs, all_products=all_products,
                           items=items, items_page=items_page, items_per_page=items_per_page,
                           items_total=items_total, items_total_pages=items_total_pages)
    if bom.bom_type == 'EBOM':
        derived_mboms = Bom.query.filter_by(source_ebom_id=bom.id).order_by(Bom.created_at.desc()).all()
    return render_template('boms/view.html', bom=bom, products=products, derived_mboms=derived_mboms)


@bom_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_bom(id):
    bom = Bom.query.get_or_404(id)
    if bom.status == 'released':
        flash('已发布的 BOM 不能编辑，请先作废或创建新版本', 'warning')
        return redirect(url_for('boms.view_bom', id=id))
    if request.method == 'POST':
        bom.name = request.form['name']
        bom.description = request.form.get('description', '')
        bom.bom_type = request.form.get('bom_type', 'EBOM')
        bom.product_id = request.form.get('product_id', type=int)
        # 重建物料明细
        BomItem.query.filter_by(bom_id=bom.id).delete()
        prod_ids = request.form.getlist('product_id[]')
        qtys = request.form.getlist('qty[]')
        units = request.form.getlist('unit[]')
        notes = request.form.getlist('note[]')
        for i, (pid, qty, unit) in enumerate(zip(prod_ids, qtys, units)):
            if pid and qty:
                note = notes[i] if i < len(notes) else ''
                bi = BomItem(bom_id=bom.id, product_id=int(pid),
                             quantity=float(qty), unit=unit, seq=i + 1, note=note)
                db.session.add(bi)
        db.session.commit()
        flash('BOM 已更新', 'success')
        return redirect(url_for('boms.view_bom', id=id))
    products = Product.query.all()
    return render_template('boms/create.html', bom=bom, products=products, edit_mode=True)


@bom_bp.route('/<int:id>/release', methods=['POST'])
@login_required
def release_bom(id):
    bom = Bom.query.get_or_404(id)
    if bom.status == 'released':
        flash('该 BOM 已经发布', 'warning')
    elif bom.items.count() == 0:
        flash('BOM 没有物料明细，不能发布', 'warning')
    else:
        bom.status = 'released'
        # 如果是 eBOM，提示可以转换为 mBOM
        if bom.bom_type == 'EBOM':
            db.session.commit()
            flash(f'eBOM「{bom.name}」已发布。可点击"转为 mBOM"创建制造 BOM。', 'success')
        else:
            db.session.commit()
            flash(f'mBOM「{bom.name}」已发布。可推送到 Odoo 系统。', 'success')
        return redirect(url_for('boms.view_bom', id=id))
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/obsolete', methods=['POST'])
@login_required
def obsolete_bom(id):
    bom = Bom.query.get_or_404(id)
    bom.status = 'obsolete'
    db.session.commit()
    flash(f'BOM「{bom.name}」已作废', 'success')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_bom(id):
    bom = Bom.query.get_or_404(id)
    if bom.status == 'released' and current_user.role != 'admin':
        flash('已发布的 BOM 需管理员才能删除', 'warning')
        return redirect(url_for('boms.view_bom', id=id))
    # 检查是否有派生 mBOM
    if bom.bom_type == 'EBOM':
        derived = Bom.query.filter_by(source_ebom_id=id).all()
        if derived:
            flash(f'该 eBOM 已有 {len(derived)} 个派生 mBOM，请先删除 mBOM 再删除 eBOM', 'warning')
            return redirect(url_for('boms.view_bom', id=id))
    BomItem.query.filter_by(bom_id=id).delete()
    BomConversion.query.filter(db.or_(
        BomConversion.source_bom_id == id,
        BomConversion.target_bom_id == id
    )).delete()
    db.session.delete(bom)
    db.session.commit()
    flash(f'BOM「{bom.name}」已删除', 'success')
    return redirect(url_for('boms.list_boms'))


# ── 4a. eBOM → mBOM 结构转换 ──
@bom_bp.route('/<int:id>/convert-to-mbom', methods=['GET', 'POST'])
@login_required
def convert_to_mbom(id):
    """将 eBOM 结构变换为 mBOM（仅做结构变换，不含工序/工时/损耗率）"""
    ebom = Bom.query.get_or_404(id)
    if ebom.bom_type != 'EBOM':
        flash('只有 eBOM 才能转换为 mBOM', 'warning')
        return redirect(url_for('boms.view_bom', id=id))
    if ebom.status != 'released':
        flash('只有已发布的 eBOM 才能转换为 mBOM', 'warning')
        return redirect(url_for('boms.view_bom', id=id))

    if request.method == 'POST':
        # 创建 mBOM，初始结构 = eBOM 结构
        mbom = Bom(
            name=request.form.get('name', ebom.name + ' - 制造BOM'),
            description=request.form.get('description', f'由 {ebom.bom_no} 结构变换而来'),
            bom_type='MBOM',
            product_id=ebom.product_id,
            source_ebom_id=ebom.id,
            created_by=current_user.id
        )
        db.session.add(mbom)
        db.session.flush()

        # 复制 eBOM 物料明细到 mBOM（可在此调整结构）
        item_count = 0
        for item in ebom.items:
            bi = BomItem(
                bom_id=mbom.id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit=item.unit,
                reference=item.reference,
                seq=item.seq
            )
            db.session.add(bi)
            item_count += 1

        # 记录转换历史
        conv = BomConversion(
            source_bom_id=ebom.id,
            target_bom_id=mbom.id,
            converted_by=current_user.id,
            conversion_note=request.form.get('conversion_note', f'标准结构变换 — {item_count} 项物料')
        )
        db.session.add(conv)
        db.session.commit()
        flash(f'mBOM「{mbom.name}」已从 eBOM 创建成功，共 {item_count} 项物料。请进入详情页调整结构后发布。', 'success')
        return redirect(url_for('boms.view_bom', id=mbom.id))

    # GET: 展示转换预览页
    return render_template('boms/convert.html', ebom=ebom)


# ── 4b. mBOM 推送到 Odoo（纯单向，只推不取） ──
@bom_bp.route('/<int:id>/push-to-odoo', methods=['POST'])
@login_required
def push_to_odoo(id):
    """将 mBOM 单向推送到 Odoo（PLM → Odoo，不拉取数据）"""
    bom = Bom.query.get_or_404(id)
    if bom.bom_type != 'MBOM':
        flash('只有 mBOM 才能推送到 Odoo', 'warning')
        return redirect(url_for('boms.view_bom', id=id))
    if bom.status != 'released':
        flash('只有已发布的 mBOM 才能推送到 Odoo', 'warning')
        return redirect(url_for('boms.view_bom', id=id))

    # 查找活跃的 Odoo 配置
    odoo_config = IntegrationConfig.query.filter_by(
        system_type='odoo', is_active=True
    ).first()

    if not odoo_config:
        flash('未找到活跃的 Odoo 集成配置，请先在「系统集成」中添加并启用 Odoo 连接', 'warning')
        return redirect(url_for('boms.view_bom', id=id))

    # 尝试推送到 Odoo
    push_success = False
    push_message = ''
    items_pushed = 0

    try:
        if odoo_config.api_url:
            import json, urllib.request, urllib.error
            # 构造推送数据
            payload = {
                'source': 'PLM',
                'bom_no': bom.bom_no,
                'name': bom.name,
                'description': bom.description or '',
                'version': bom.version,
                'product_code': bom.product.code if bom.product else '',
                'product_name': bom.product.name if bom.product else '',
                'items': []
            }
            for item in bom.items:
                payload['items'].append({
                    'product_code': item.product.code,
                    'product_name': item.product.name,
                    'quantity': item.quantity,
                    'unit': item.unit,
                    'note': item.note or ''
                })
            items_pushed = len(payload['items'])

            # 发送请求
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                odoo_config.api_url + '/api/bom/import',
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {odoo_config.api_key or ""}'
                },
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    push_success = True
                    push_message = f'成功推送 {items_pushed} 项物料到 Odoo'
                else:
                    push_message = f'Odoo 返回 HTTP {resp.status}'
        else:
            # 无 API 地址时模拟推送（开发/演示环境）
            push_success = True
            items_pushed = bom.items.count()
            push_message = f'[模拟] 成功推送 {items_pushed} 项物料到 Odoo（未配置 API 地址，实际推送需配置）'
    except urllib.error.URLError as e:
        push_message = f'连接 Odoo 失败: {str(e.reason)}'
    except Exception as e:
        push_message = f'推送异常: {str(e)}'

    # 更新同步状态
    bom.sync_status = 'synced' if push_success else 'sync_failed'
    bom.sync_time = datetime.now()
    bom.sync_message = push_message

    # 记录同步日志
    log = SyncLog(
        integration_id=odoo_config.id,
        direction='export',
        status='success' if push_success else 'failed',
        records_count=items_pushed,
        message=f'BOM {bom.bom_no}: {push_message}'
    )
    odoo_config.last_sync = datetime.now()
    db.session.add(log)
    db.session.commit()

    if push_success:
        flash(f'<i class="bi bi-check-circle me-1"></i>mBOM「{bom.name}」已成功推送到 Odoo！{items_pushed} 项物料已同步。', 'success')
    else:
        flash(f'<i class="bi bi-exclamation-triangle me-1"></i>推送失败：{push_message}', 'danger')
    return redirect(url_for('boms.view_bom', id=id))


# ── BOM v2: 文档关联 / 审批 / 版本对比 / 替代料 ──

@bom_bp.route('/<int:id>/attach-doc', methods=['POST'])
@login_required
def bom_attach_doc(id):
    bom = Bom.query.get_or_404(id)
    doc_id = request.form.get('document_id', type=int)
    doc = Document.query.get_or_404(doc_id)
    existing = BomDocument.query.filter_by(bom_id=id, document_id=doc_id).first()
    if existing:
        flash('该文档已关联', 'warning')
    else:
        db.session.add(BomDocument(bom_id=id, document_id=doc_id))
        db.session.commit()
        flash(f'已关联文档：{doc.title[:30]}', 'success')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/detach-doc/<int:doc_id>', methods=['POST'])
@login_required
def bom_detach_doc(id, doc_id):
    bd = BomDocument.query.filter_by(bom_id=id, document_id=doc_id).first_or_404()
    db.session.delete(bd)
    db.session.commit()
    flash('已取消关联', 'info')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/submit-approval', methods=['POST'])
@login_required
def bom_submit_approval(id):
    bom = Bom.query.get_or_404(id)
    if bom.status in ('review', 'approved', 'released'):
        flash('已在审批中或已发布', 'warning')
        return redirect(url_for('boms.view_bom', id=id))
    # 清空旧审批记录（防重复）
    BomApproval.query.filter_by(bom_id=id).delete()
    bom.status = 'review'
    managers = User.query.filter_by(role='manager').all()
    for i, m in enumerate(managers):
        db.session.add(BomApproval(bom_id=id, step=i + 1, approver_id=m.id))
    db.session.commit()
    flash('已提交 BOM 审批', 'success')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/approve-step/<int:step>', methods=['POST'])
@login_required
def bom_approve_step(id, step):
    appr = BomApproval.query.filter_by(bom_id=id, step=step, status='pending').first_or_404()
    if appr.approver_id != current_user.id:
        flash('非当前审批人，无法审批', 'danger')
        return redirect(url_for('boms.view_bom', id=id))
    appr.status = 'approved'
    appr.decided_at = datetime.now()
    appr.comment = request.form.get('comment', '')

    # 检查是否全部通过
    remaining = BomApproval.query.filter_by(bom_id=id, status='pending').count()
    if remaining == 0:
        Bom.query.filter_by(id=id).update({'status': 'approved'})
    db.session.commit()
    flash(f'步骤 {step} 审批通过', 'success')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/<int:id>/reject-step/<int:step>', methods=['POST'])
@login_required
def bom_reject_step(id, step):
    appr = BomApproval.query.filter_by(bom_id=id, step=step, status='pending').first_or_404()
    appr.status = 'rejected'
    appr.decided_at = datetime.now()
    appr.comment = request.form.get('comment', '')
    Bom.query.filter_by(id=id).update({'status': 'rejected'})
    db.session.commit()
    flash(f'步骤 {step} 已驳回', 'danger')
    return redirect(url_for('boms.view_bom', id=id))


@bom_bp.route('/compare')
@login_required
def bom_compare():
    """BOM 版本对比"""
    bom_a_id = request.args.get('a', type=int)
    bom_b_id = request.args.get('b', type=int)
    bom_a = Bom.query.get(bom_a_id) if bom_a_id else None
    bom_b = Bom.query.get(bom_b_id) if bom_b_id else None

    diff = []
    if bom_a and bom_b:
        a_items = {i.product.code: i for i in bom_a.items.all()}
        b_items = {i.product.code: i for i in bom_b.items.all()}
        all_codes = set(a_items.keys()) | set(b_items.keys())
        for code in sorted(all_codes):
            a = a_items.get(code)
            b = b_items.get(code)
            if a and b:
                change = 'same' if abs(a.quantity - b.quantity) < 0.001 else 'changed'
                if a.note != b.note:
                    change = 'changed'
            elif a and not b:
                change = 'removed'
            else:
                change = 'added'
            diff.append({
                'code': code,
                'name': (a or b).product.name,
                'a_qty': a.quantity if a else None,
                'b_qty': b.quantity if b else None,
                'a_note': a.note if a else '',
                'b_note': b.note if b else '',
                'unit': (a or b).unit,
                'change': change
            })

    all_boms = Bom.query.filter_by(bom_type='EBOM').order_by(Bom.name).all()
    return render_template('boms/compare.html', bom_a=bom_a, bom_b=bom_b,
                           diff=diff, all_boms=all_boms)


@bom_bp.route('/<int:id>/item/<int:item_id>/substitute', methods=['POST'])
@login_required
def bom_set_substitute(id, item_id):
    item = BomItem.query.filter_by(id=item_id, bom_id=id).first_or_404()
    sub_id = request.form.get('substitute_product_id', type=int)
    item.substitute_product_id = sub_id if sub_id else None
    db.session.commit()
    flash('替代料已更新', 'success')
    return redirect(url_for('boms.view_bom', id=id))


# ════════════════════════════════
#  5. 项目管理
# ════════════════════════════════
@project_bp.route('/')
@login_required
def list_projects():
    projects = Project.query.order_by(Project.created_at.desc()).all()
    # 用 SQL 聚合查询预计算每个项目的任务数（兼容所有 SQLAlchemy 版本）
    from sqlalchemy import func
    rows = db.session.query(Task.project_id, func.count(Task.id)).group_by(Task.project_id).all()
    task_counts = {pid: cnt for pid, cnt in rows}
    return render_template('projects/list.html', projects=projects, task_counts=task_counts)


@project_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_project():
    if request.method == 'POST':
        p = Project(
            name=request.form['name'],
            description=request.form.get('description', ''),
            priority=request.form.get('priority', 'medium'),
            manager_id=request.form.get('manager_id', type=int),
            start_date=datetime.strptime(request.form['start_date'], '%Y-%m-%d').date() if request.form.get('start_date') else None,
            end_date=datetime.strptime(request.form['end_date'], '%Y-%m-%d').date() if request.form.get('end_date') else None,
            created_by=current_user.id
        )
        db.session.add(p)
        db.session.commit()
        flash('项目创建成功', 'success')
        return redirect(url_for('projects.list_projects'))
    users = User.query.all()
    return render_template('projects/create.html', users=users)


@project_bp.route('/<int:id>')
@login_required
def view_project(id):
    p = Project.query.get_or_404(id)
    users = User.query.order_by(User.display_name).all()
    return render_template('projects/view.html', project=p, users=users)


@project_bp.route('/<int:id>/add-task', methods=['POST'])
@login_required
def add_task(id):
    t = Task(
        project_id=id,
        name=request.form['name'],
        description=request.form.get('description', ''),
        assignee_id=request.form.get('assignee_id', type=int),
        priority=request.form.get('priority', 'medium'),
        start_date=datetime.strptime(request.form['start_date'], '%Y-%m-%d').date() if request.form.get('start_date') else None,
        due_date=datetime.strptime(request.form['due_date'], '%Y-%m-%d').date() if request.form.get('due_date') else None,
    )
    db.session.add(t)
    db.session.commit()
    flash('任务添加成功', 'success')
    return redirect(url_for('projects.view_project', id=id))


@project_bp.route('/task/<int:id>/update-status', methods=['POST'])
@login_required
def update_task_status(id):
    t = Task.query.get_or_404(id)
    t.status = request.form['status']
    if t.status == 'done':
        t.completed_at = datetime.now()
    db.session.commit()
    return redirect(url_for('projects.view_project', id=t.project_id))


# ════════════════════════════════
#  6. 变更管理
# ════════════════════════════════
@change_bp.route('/')
@login_required
def list_changes():
    ecrs = ChangeRequest.query.order_by(ChangeRequest.created_at.desc()).all()
    return render_template('changes/list.html', ecrs=ecrs)


@change_bp.route('/create-ecr', methods=['GET', 'POST'])
@login_required
def create_ecr():
    if request.method == 'POST':
        ecr = ChangeRequest(
            title=request.form['title'],
            description=request.form.get('description', ''),
            reason=request.form.get('reason', ''),
            priority=request.form.get('priority', 'medium'),
            applicant_id=current_user.id,
            assignee_id=request.form.get('assignee_id', type=int)
        )
        db.session.add(ecr)
        db.session.commit()
        flash('变更申请已提交', 'success')
        return redirect(url_for('changes.list_changes'))
    users = User.query.all()
    return render_template('changes/create_ecr.html', users=users)


@change_bp.route('/<int:id>')
@login_required
def view_change(id):
    ecr = ChangeRequest.query.get_or_404(id)
    return render_template('changes/view.html', ecr=ecr)


@change_bp.route('/<int:id>/submit', methods=['POST'])
@login_required
def submit_ecr(id):
    ecr = ChangeRequest.query.get_or_404(id)
    ecr.status = 'submitted'
    db.session.commit()
    flash('变更申请已提交审核', 'success')
    return redirect(url_for('changes.view_change', id=id))


@change_bp.route('/<int:id>/approve-ecr', methods=['POST'])
@login_required
def approve_ecr(id):
    ecr = ChangeRequest.query.get_or_404(id)
    action = request.form['action']
    if action == 'approved':
        ecr.status = 'approved'
    elif action == 'rejected':
        ecr.status = 'rejected'
    db.session.commit()
    flash(f'变更申请已{action}', 'success')
    return redirect(url_for('changes.view_change', id=id))


@change_bp.route('/<int:id>/create-eco', methods=['POST'])
@login_required
def create_eco(id):
    ecr = ChangeRequest.query.get_or_404(id)
    eco = ChangeOrder(
        ecr_id=id,
        title=f'执行: {ecr.title}',
        description=request.form.get('description', ''),
        impact_analysis=request.form.get('impact_analysis', ''),
        affected_boms=request.form.get('affected_boms', ''),
        affected_docs=request.form.get('affected_docs', ''),
        executor_id=current_user.id
    )
    ecr.status = 'analyzing'
    db.session.add(eco)
    db.session.commit()
    flash('变更通知已创建', 'success')
    return redirect(url_for('changes.view_change', id=id))


@change_bp.route('/eco/<int:id>/complete', methods=['POST'])
@login_required
def complete_eco(id):
    eco = ChangeOrder.query.get_or_404(id)
    eco.status = 'completed'
    eco.completed_at = datetime.now()
    ecr = eco.ecr
    ecr.status = 'closed'
    db.session.commit()
    flash('变更已执行完成', 'success')
    return redirect(url_for('changes.view_change', id=eco.ecr_id))


# ════════════════════════════════
#  7. 工时管理
# ════════════════════════════════
@workhour_bp.route('/')
@login_required
def list_work_hours():
    whs = WorkHourStandard.query.all()
    products = Product.query.all()
    return render_template('work_hours/list.html', whs=whs, products=products)


@workhour_bp.route('/create', methods=['POST'])
@login_required
def create_work_hour():
    wh = WorkHourStandard(
        product_id=request.form.get('product_id', type=int),
        process_name=request.form['process_name'],
        standard_hours=float(request.form['standard_hours']),
        unit=request.form.get('unit', '小时'),
        machine_type=request.form.get('machine_type', ''),
        labor_type=request.form.get('labor_type', ''),
        note=request.form.get('note', '')
    )
    db.session.add(wh)
    db.session.commit()
    flash('工时标准创建成功', 'success')
    return redirect(url_for('work_hours.list_work_hours'))


# ════════════════════════════════
#  8. 工艺管理
# ════════════════════════════════
@process_bp.route('/')
@login_required
def list_processes():
    routes = ProcessRoute.query.all()
    return render_template('processes/list.html', routes=routes)


@process_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_process():
    if request.method == 'POST':
        pr = ProcessRoute(
            name=request.form['name'],
            product_id=request.form.get('product_id', type=int),
            description=request.form.get('description', ''),
            created_by=current_user.id
        )
        db.session.add(pr)
        db.session.flush()
        names = request.form.getlist('step_name[]')
        hours = request.form.getlist('step_hours[]')
        centers = request.form.getlist('step_center[]')
        for i, (n, h, c) in enumerate(zip(names, hours, centers)):
            if n.strip():
                step = ProcessStep(
                    route_id=pr.id, seq=i + 1, name=n,
                    standard_hours=float(h) if h else 0,
                    work_center=c
                )
                db.session.add(step)
        db.session.commit()
        flash('工艺路线创建成功', 'success')
        return redirect(url_for('processes.list_processes'))
    products = Product.query.all()
    return render_template('processes/create.html', products=products)


@process_bp.route('/<int:id>')
@login_required
def view_process(id):
    route = ProcessRoute.query.get_or_404(id)
    return render_template('processes/view.html', route=route)


# ════════════════════════════════
#  9. 集成配置 — PLM → Odoo 纯单向推送
# ════════════════════════════════
@integration_bp.route('/')
@login_required
def list_integrations():
    configs = IntegrationConfig.query.all()
    # 待推送的 mBOM 数量
    pending_count = Bom.query.filter_by(
        bom_type='MBOM', status='released', sync_status='not_synced'
    ).count()
    # 最近同步日志
    recent_logs = SyncLog.query.order_by(SyncLog.created_at.desc()).limit(10).all()
    return render_template('integrations/list.html', configs=configs,
                           pending_count=pending_count, recent_logs=recent_logs)


@integration_bp.route('/create', methods=['POST'])
@login_required
def create_integration():
    ic = IntegrationConfig(
        name=request.form['name'],
        system_type=request.form['system_type'],
        api_url=request.form.get('api_url', ''),
        api_key=request.form.get('api_key', ''),
        is_active=request.form.get('is_active') == 'on'
    )
    db.session.add(ic)
    db.session.commit()
    flash('集成配置已创建', 'success')
    return redirect(url_for('integrations.list_integrations'))


@integration_bp.route('/<int:id>/sync', methods=['POST'])
@login_required
def sync_odoo(id):
    """mBOM 单向推送到 Odoo（PLM → Odoo，不拉取数据）"""
    ic = IntegrationConfig.query.get_or_404(id)
    direction = request.form.get('direction', 'export')

    if direction == 'import':
        flash('PLM 系统采用纯单向架构，不支持从 Odoo 拉取数据。请使用 mBOM 推送功能。', 'warning')
        return redirect(url_for('integrations.list_integrations'))

    # 导出：批量推送已发布但未同步的 mBOM
    pending_boms = Bom.query.filter_by(
        bom_type='MBOM', status='released', sync_status='not_synced'
    ).all()

    if not pending_boms:
        flash('没有待推送的 mBOM，所有已发布的 mBOM 均已同步。', 'info')
        return redirect(url_for('integrations.list_integrations'))

    success_count = 0
    fail_count = 0
    for bom in pending_boms:
        try:
            items_count = bom.items.count()
            # 实际环境中调用 Odoo API
            if ic.api_url:
                import json, urllib.request, urllib.error
                payload = {
                    'source': 'PLM',
                    'bom_no': bom.bom_no,
                    'name': bom.name,
                    'version': bom.version,
                    'product_code': bom.product.code if bom.product else '',
                    'items': [{'product_code': it.product.code, 'product_name': it.product.name,
                              'quantity': it.quantity, 'unit': it.unit} for it in bom.items]
                }
                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    ic.api_url + '/api/bom/import',
                    data=data,
                    headers={'Content-Type': 'application/json',
                             'Authorization': f'Bearer {ic.api_key or ""}'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        bom.sync_status = 'synced'
                        success_count += 1
                    else:
                        bom.sync_status = 'sync_failed'
                        fail_count += 1
            else:
                # 模拟推送
                bom.sync_status = 'synced'
                success_count += 1
            bom.sync_time = datetime.now()
            bom.sync_message = f'批量推送成功（{items_count} 项物料）'
        except Exception as e:
            bom.sync_status = 'sync_failed'
            bom.sync_message = str(e)
            fail_count += 1

    # 记录同步日志
    log = SyncLog(
        integration_id=id,
        direction='export',
        status='success' if fail_count == 0 else ('failed' if success_count == 0 else 'partial'),
        records_count=success_count,
        message=f'批量推送完成：成功 {success_count}，失败 {fail_count}（共 {len(pending_boms)} 个 mBOM）'
    )
    ic.last_sync = datetime.now()
    db.session.add(log)
    db.session.commit()

    if fail_count == 0:
        flash(f'<i class="bi bi-check-circle me-1"></i>{success_count} 个 mBOM 全部推送成功！', 'success')
    else:
        flash(f'推送完成：成功 {success_count} 个，失败 {fail_count} 个', 'warning')
    return redirect(url_for('integrations.list_integrations'))


@integration_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_integration(id):
    ic = IntegrationConfig.query.get_or_404(id)
    db.session.delete(ic)
    db.session.commit()
    flash(f'集成配置「{ic.name}」已删除', 'success')
    return redirect(url_for('integrations.list_integrations'))
