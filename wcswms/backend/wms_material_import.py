"""Preview and atomically merge material master data; never post inventory."""
import base64
import csv
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from .wms_store import WmsError, MATERIAL_FIELDS, material_details

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
HEADERS = {'物料编码': 'sku', '物料名称': 'material_name', '型号': 'model', '规格': 'specification',
           'sku': 'sku', 'material_name': 'material_name', 'model': 'model', 'specification': 'specification'}
LIMITS = {'sku': 80, 'material_name': 120, 'model': 120, 'specification': 200}
LABELS = {'sku': '物料编码', 'material_name': '物料名称', 'model': '型号', 'specification': '规格'}


def parse_file(filename, content):
    try:
        raw = base64.b64decode(content, validate=True)
    except ValueError as error:
        raise WmsError('文件内容无效，请重新选择文件') from error
    if not raw or len(raw) > MAX_BYTES:
        raise WmsError('请选择非空文件，文件不能超过 2 MB')
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == '.xlsx':
            from openpyxl import load_workbook
            with ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 200 or sum(info.file_size for info in archive.infolist()) > 20 * MAX_BYTES:
                    raise WmsError('Excel 文件展开后过大，请仅保留物料表')
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                sheet = book.worksheets[0]
                if sheet.max_row and sheet.max_row > MAX_ROWS + 1:
                    raise WmsError('Excel 数据须位于首 1001 行（含表头），请分批导入并删除多余空行')
                if sheet.max_column and sheet.max_column > 4:
                    raise WmsError('Excel 仅支持四列物料信息，请使用模板')
                # Ignore inflated worksheet dimensions; bound iteration independently.
                sheet.reset_dimensions()
                rows = []
                for number, cells in enumerate(sheet.iter_rows(max_row=MAX_ROWS + 2, max_col=5), 1):
                    values = [cell.value for cell in cells]
                    if any(cell.data_type == 'f' for cell in cells):
                        raise WmsError('Excel 含公式，请粘贴为文本值后导入')
                    if any(value is not None for value in values):
                        if number > MAX_ROWS + 1:
                            raise WmsError('Excel 数据须位于首 1001 行（含表头），请分批导入')
                        if values[4] is not None:
                            raise WmsError('Excel 仅支持四列物料信息，请使用模板')
                        rows.append((number, values[:4]))
            finally:
                book.close()
        elif suffix == '.csv':
            try:
                source = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                source = raw.decode('gb18030')
            reader = csv.reader(io.StringIO(source, newline=''), strict=True)
            rows = []
            for values in reader:
                if any(value.strip() for value in values):
                    rows.append((reader.line_num, values))
                if len(rows) > MAX_ROWS + 1:
                    raise WmsError('每次最多导入 1000 条物料，请分批导入')
        else:
            raise WmsError('支持 .xlsx 或 .csv，请将旧版 .xls 另存为 .xlsx')
    except WmsError:
        raise
    except Exception as error:
        raise WmsError('无法读取文件，请使用模板重新保存为 .xlsx 或 CSV（UTF-8）') from error
    if len(rows) < 2:
        raise WmsError('文件没有物料数据，请在表头下填写物料信息')
    if len(rows) > MAX_ROWS + 1:
        raise WmsError('每次最多导入 1000 条物料，请分批导入')
    header = rows[0][1]
    # XLSX trailing blank columns are harmless; unknown populated columns are not.
    while header and header[-1] in (None, ''):
        header.pop()
    keys = [HEADERS.get(str(value or '').strip().lower()) for value in header]
    if not keys or any(key is None for key in keys) or len(set(keys)) != len(keys) or 'sku' not in keys:
        raise WmsError('表头须含“物料编码”；其余支持“物料名称、型号、规格”，不可重复或包含其他列')
    result = []
    for number, values in rows[1:]:
        item = dict(row=number, sku='', **material_details({}), errors=[])
        if any(value not in (None, '') for value in values[len(keys):]):
            item['errors'].append('数据列数超过表头，请检查分隔符或额外列')
        for index, key in enumerate(keys):
            value = values[index] if index < len(values) else ''
            if key == 'sku' and value is not None and not isinstance(value, str):
                item['errors'].append('物料编码须设置为文本，避免丢失前导零或变成科学计数法')
            text = '' if value is None else str(value).strip()
            item[key] = text
            if len(text) > LIMITS[key]:
                item['errors'].append(f'{LABELS[key]}超过 {LIMITS[key]} 个字符')
            if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
                item['errors'].append('含有不可识别的控制字符')
        if not item['sku']:
            item['errors'].append('物料编码不能为空')
        result.append(item)
    counts = Counter(item['sku'] for item in result)
    for item in result:
        if item['sku'] and counts[item['sku']] > 1:
            item['errors'].append('文件内物料编码重复，请合并为一行')
    return result


def preview(state, rows):
    catalog = state.get('materials', {})
    active = {row['sku'] for order in state.get('orders', []) if order['status'] not in {'COMPLETED', 'CANCELLED'}
              for row in [order, *order.get('before_contents', []), *order.get('after_contents', [])]}
    items = []
    for row in rows:
        item = {**row, 'errors': list(row['errors'])}
        existing = catalog.get(row['sku'])
        item['status'] = 'NEW' if existing is None else 'UNCHANGED'
        if existing:
            for field in MATERIAL_FIELDS:
                if row[field] and existing.get(field) and row[field] != existing[field]:
                    item['errors'].append(f'{LABELS[field]}与已有档案不同，请先核对修订')
                elif row[field] and not existing.get(field):
                    item['status'] = 'FILL'
            if item['status'] == 'FILL' and row['sku'] in active:
                item['errors'].append('该物料有未结束单据，完成后再补全档案')
        if item['errors']:
            item['status'] = 'ERROR'
        items.append(item)
    snapshot = {row['sku']: {'material': catalog.get(row['sku']), 'active_order': row['sku'] in active} for row in rows}
    token = hashlib.sha256(json.dumps([rows, snapshot], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    counts = dict.fromkeys(('NEW', 'FILL', 'UNCHANGED', 'ERROR'), 0)
    for item in items:
        counts[item['status']] += 1
    return dict(rows=items, counts=counts, total=len(items), token=token, valid=not counts['ERROR'])


def import_materials(store, body):
    if not body['confirmed']:
        raise WmsError('请先预览并确认导入物料信息')
    rows = parse_file(body['filename'], body['content'])
    payload = dict(operation='material-import', file_hash=hashlib.sha256(body['content'].encode()).hexdigest(),
                   expected_token=body['expected_token'])
    def apply(state):
        checked = preview(state, rows)
        if checked['token'] != body['expected_token']:
            raise WmsError('物料档案或单据已变化，请重新预览后导入')
        if not checked['valid']:
            raise WmsError('存在错误行，整批未导入；请修正文件后重新预览')
        for row in checked['rows']:
            if row['status'] in {'NEW', 'FILL'}:
                store.register_material(state, row)
        counts = checked['counts']
        store.event(state, f'物料导入：新增 {counts["NEW"]}，补全 {counts["FILL"]}，相同跳过 {counts["UNCHANGED"]}')
        return dict(total=checked['total'], counts=counts)
    return store.once(body['request_id'], payload, apply)


def template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = '物料信息'
    sheet.append(['物料编码', '物料名称', '型号', '规格'])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='167565')
    for key, width in zip('ABCD', (26, 32, 28, 45)):
        sheet.column_dimensions[key].width = width
    for row in sheet.iter_rows(min_row=2, max_row=MAX_ROWS + 1, max_col=4):
        for cell in row:
            cell.number_format = '@'
    sheet.freeze_panes = 'A2'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
