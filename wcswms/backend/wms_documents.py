"""Read-only searches across the complete ledger, including archived documents."""
from datetime import datetime, timezone

from .wms_store import WmsError, display_location


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def records(ledger, category):
    if category == 'TASK':
        return ledger['tasks']
    return [row for row in ledger.get('orders', []) if row['kind'] == category]


def search(ledger, category, *, keyword='', status='', created_from=None, created_before=None,
           page=1, page_size=10):
    if (created_from and not created_from.tzinfo) or (created_before and not created_before.tzinfo):
        raise WmsError('查询日期须包含时区')
    if created_from and created_before and created_from >= created_before:
        raise WmsError('开始日期不能晚于结束日期')
    terms = keyword.casefold().split()
    found = []
    for row in records(ledger, category):
        stamp = _timestamp(row.get('created_at'))
        if created_from and (stamp is None or stamp < created_from):
            continue
        if created_before and (stamp is None or stamp >= created_before):
            continue
        if status and row['status'] != status:
            continue
        lines = row.get('lines') or row.get('contents') or [row]
        values = [row.get(key, '') for key in ('number', 'barcode', 'sku', 'material_name',
                  'model', 'specification', 'batch', 'note', 'count_note', 'detail')]
        for key in ('location', 'target_location'):
            values += [row.get(key, ''), display_location(row.get(key) or '')]
        for line in lines:
            values.extend(line.get(key, '') for key in ('sku', 'material_name', 'model', 'specification', 'batch'))
        for key in ('created_by', 'operator', 'counted_by'):
            actor = row.get(key) or {}
            values += [actor.get('username', ''), actor.get('display_name', '')]
        text = ' '.join(str(value or '') for value in values).casefold()
        if all(term in text for term in terms):
            found.append(row)
    found.sort(key=lambda row: (_timestamp(row.get('created_at')) or datetime.min.replace(tzinfo=timezone.utc), row.get('number', ''), row['id']), reverse=True)
    total = len(found)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    return dict(items=[{**row, 'record_type':category} for row in found[(page-1)*page_size:page*page_size]],
                total=total, page=page, pages=pages, page_size=page_size)


def detail(ledger, category, ident):
    row = next((row for row in records(ledger, category) if row['id'] == ident), None)
    if row is None:
        raise WmsError('单据不存在于当前设备，请刷新查询结果')
    related = [task for task in ledger['tasks'] if category != 'TASK' and task.get('order_id') == ident]
    related.sort(key=lambda task: (task.get('created_at', ''), task['id']))
    return dict(**row, record_type=category, related_tasks=related)
