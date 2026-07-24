"""补导入：搅拌机、固晶机、贴片机"""
import sys, os, re, glob
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app, db
from app.models import Product, Bom, BomItem, User
import xlrd, openpyxl

app = create_app()
ctx = app.app_context()
ctx.push()
admin = User.query.filter_by(username='admin').first()

existing = {p.code: p for p in Product.query.all()}
stats = {'products_created': 0, 'products_reused': 0, 'boms_created': 0, 'bom_items': 0}

def get_or_create(code, name, level=3):
    code = str(code)[:80]
    if code in existing:
        stats['products_reused'] += 1
        return existing[code]
    p = Product(code=code, name=str(name)[:200], level=level, status='active')
    db.session.add(p)
    db.session.flush()
    existing[code] = p
    stats['products_created'] += 1
    return p

def parse_excel(path):
    ext = path.rsplit('.', 1)[-1].lower()
    items = []
    if ext == 'xls':
        wb = xlrd.open_workbook(path)
        sheets = [(sn, wb.sheet_by_name(sn), 'xls') for sn in wb.sheet_names()]
    else:
        wb = openpyxl.load_workbook(path, data_only=True)
        sheets = [(sn, wb[sn], 'xlsx') for sn in wb.sheetnames]

    def cv(sh, r, c, fmt):
        try:
            if fmt == 'xls':
                return str(sh.cell_value(r, c)).strip()
            else:
                v = sh.cell(row=r + 1, column=c + 1).value
                return str(v).strip() if v is not None else ''
        except:
            return ''

    for sn, sh, fmt in sheets:
        nr = sh.nrows if fmt == 'xls' else sh.max_row
        nc = sh.ncols if fmt == 'xls' else sh.max_column
        hr = None
        for r in range(min(nr, 30)):
            if any('序号' in cv(sh, r, c, fmt) for c in range(min(nc, 16))):
                hr = r
                break
        if hr is None:
            continue
        hdr = [cv(sh, hr, c, fmt) for c in range(nc)]
        cm = {}
        for i, h in enumerate(hdr):
            if '序号' in h: cm['s'] = i
            elif '名称' in h: cm['n'] = i
            elif '规格型号' in h or '型号' in h: cm['m'] = i
            elif '品牌' in h: cm['b'] = i
            elif '用量' in h: cm['u'] = i
            elif '数量' in h and '用量' not in h: cm['q'] = i
            elif '单位' in h: cm['un'] = i
        if 'n' not in cm and 'm' not in cm:
            continue
        for r in range(hr + 1, nr):
            sv = cv(sh, r, cm.get('s', 0), fmt)
            nv = cv(sh, r, cm.get('n', 1), fmt)
            mv = cv(sh, r, cm.get('m', 2), fmt)
            if not sv or sv in ('', '0.0', '0'):
                continue
            try:
                float(sv)
            except ValueError:
                continue
            if not nv and not mv:
                continue
            u = 1.0
            if 'u' in cm:
                try:
                    u = float(cv(sh, r, cm['u'], fmt))
                except (ValueError, TypeError):
                    pass
            elif 'q' in cm:
                try:
                    u = float(cv(sh, r, cm['q'], fmt))
                except (ValueError, TypeError):
                    pass
            un = cv(sh, r, cm['un'], fmt) if 'un' in cm else '个'
            if not un:
                un = '个'
            items.append({
                'name': nv,
                'spec': mv,
                'brand': cv(sh, r, cm['b'], fmt) if 'b' in cm else '',
                'usage': u,
                'unit': un,
            })
    return items


base = r'C:\Users\15897\Desktop\机械图'
for product_dir in ['搅拌机', '固晶机', '贴片机']:
    path = os.path.join(base, product_dir)
    xls_files = (glob.glob(os.path.join(path, '**', '*.xls'), recursive=True) +
                 glob.glob(os.path.join(path, '**', '*.xlsx'), recursive=True))
    print(f'{product_dir}: found {len(xls_files)} Excel files')
    for fp in xls_files:
        if '~$' in fp:
            continue
        fname = os.path.basename(fp)
        mach = get_or_create(product_dir, product_dir, 0)

        # Extract version from path
        rel = os.path.relpath(fp, path)
        ver = 'V1'
        for part in rel.replace('\\', '/').split('/'):
            vm = re.search(r'(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})', part)
            if vm:
                ver = vm.group(1)
                break
            v2 = re.search(r'(V\d+)', part, re.I)
            if v2:
                ver = v2.group(1).upper()
                break

        short_name = fname.rsplit('.', 1)[0][:40]
        bom_name = f'{product_dir} {ver} {short_name}'
        items = parse_excel(fp)
        if not items:
            print(f'  SKIP: {fname} (no items)')
            continue

        bom = Bom(
            name=bom_name,
            description=f'从Excel导入: {fname}',
            bom_type='EBOM',
            product_id=mach.id,
            created_by=admin.id,
            status='draft'
        )
        db.session.add(bom)
        db.session.flush()

        # Merge by spec
        merged = {}
        for it in items:
            k = it['spec'] if it['spec'] else it['name']
            if k in merged:
                merged[k]['usage'] += it['usage']
            else:
                merged[k] = it

        for seq, (k, it) in enumerate(merged.items(), 1):
            spec = it['spec'] if it['spec'] else it['name']
            p = get_or_create(spec, it['name'])
            if not p:
                continue
            db.session.add(BomItem(
                bom_id=bom.id, product_id=p.id,
                quantity=it['usage'], unit=it['unit'], seq=seq
            ))
        stats['boms_created'] += 1
        stats['bom_items'] += len(merged)
        print(f'  OK: {bom_name} ({len(merged)} items)')

    db.session.commit()

print(f'\nDone! New products: {stats["products_created"]}, reused: {stats["products_reused"]}')
print(f'BOMs: {stats["boms_created"]}, items: {stats["bom_items"]}')
ctx.pop()
