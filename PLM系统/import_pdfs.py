"""导入机械图目录下所有 PDF 文件到图文档案"""
import sys, os, glob, shutil, re
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app, db
from app.models import Document, DocumentCategory, User

app = create_app()
ctx = app.app_context()
ctx.push()
admin = User.query.filter_by(username='admin').first()

base = r'C:\Users\15897\Desktop\机械图'
PDF_DIR = os.path.join(os.path.dirname(__file__), 'app', 'static', 'uploads', 'pdf')
os.makedirs(PDF_DIR, exist_ok=True)

# 分类映射
categories = {
    '五金件加工图纸': DocumentCategory.query.filter_by(name='五金件加工图纸').first(),
    '钣金加工图纸': DocumentCategory.query.filter_by(name='钣金加工图纸').first(),
    '标准件图纸': DocumentCategory.query.filter_by(name='标准件图纸').first(),
    'PDF图纸': DocumentCategory.query.filter_by(name='PDF图纸').first(),
    'CAD图纸': DocumentCategory.query.filter_by(name='CAD图纸').first(),
    '3D模型': DocumentCategory.query.filter_by(name='3D模型').first(),
    '其他图纸': DocumentCategory.query.filter_by(name='其他图纸').first(),
}
default_cat = categories['PDF图纸']

def guess_category(rel_path):
    """根据路径关键词判断分类"""
    parts = rel_path.replace('\\', '/').split('/')
    for p in parts:
        if '五金' in p: return categories['五金件加工图纸']
        if '钣金' in p: return categories['钣金加工图纸']
        if '标准件' in p: return categories['标准件图纸']
        if 'CAD' in p.upper(): return categories['CAD图纸']
        if '3D' in p or 'STEP' in p.upper(): return categories['3D模型']
    return default_cat

pdfs = glob.glob(os.path.join(base, '**', '*.pdf'), recursive=True) + glob.glob(os.path.join(base, '**', '*.PDF'), recursive=True)
print(f'Total PDFs: {len(pdfs)}')

# 排除已有记录的（按文件名匹配）
existing_names = {d.file_name for d in Document.query.filter(Document.file_name.isnot(None)).all()}

batch_size = 500
imported = 0
skipped = 0
errors = 0
batch = []

for i, p in enumerate(pdfs):
    fname = os.path.basename(p)
    size = os.path.getsize(p)

    # 跳过过小的（<5KB，通常不是有效图纸）
    if size < 5000:
        skipped += 1
        continue

    # 跳过已存在的
    if fname in existing_names:
        skipped += 1
        continue

    # 复制文件
    safe_name = fname.replace(' ', '_')
    dest = os.path.join(PDF_DIR, safe_name)
    # 处理重名
    counter = 1
    base_name, ext = os.path.splitext(safe_name)
    while os.path.exists(dest):
        dest = os.path.join(PDF_DIR, f'{base_name}_{counter}{ext}')
        counter += 1
        if counter > 50:
            break
    if counter > 50:
        skipped += 1
        continue

    try:
        shutil.copy2(p, dest)
    except Exception as e:
        errors += 1
        continue

    rel = os.path.relpath(p, base)
    cat = guess_category(rel)
    file_path = f'pdf/{os.path.basename(dest)}'

    title = os.path.splitext(fname)[0][:100]

    batch.append(Document(
        title=title,
        description=f'PDF 导入 | 源: {rel[:200]}',
        file_name=fname,
        file_path=file_path,
        file_size=size,
        category_id=cat.id if cat else None,
        status='published',
        author_id=admin.id,
        tags=f'PDF,{cat.name if cat else "未分类"}',
        version='1.0'
    ))
    imported += 1
    existing_names.add(fname)

    if len(batch) >= batch_size:
        db.session.bulk_save_objects(batch)
        db.session.commit()
        print(f'  Batch {imported // batch_size}: {imported} PDFs imported, {skipped} skipped')
        batch = []

    if imported >= 8000:  # 安全上限
        print(f'  Reached 8000 limit, stopping')
        break

# 最后一批
if batch:
    db.session.bulk_save_objects(batch)
    db.session.commit()

total = Document.query.count()
with_file = Document.query.filter(Document.file_path.isnot(None), Document.file_path != '').count()
print(f'\nDone! Imported {imported} PDFs, skipped {skipped}, errors {errors}')
print(f'Total docs: {total}, with files: {with_file}')
ctx.pop()
