"""Per-material box contents, with a lazy adapter for existing single-material stock."""
import copy
import json

from .wms_store import WmsError, material_details


def contents(record):
    if 'contents' in record:
        return copy.deepcopy(record['contents'])
    if record.get('container_type') == 'EMPTY_BIN' or not record.get('sku') or not record.get('quantity'):
        return []
    return [dict(sku=record['sku'], quantity=record['quantity'], batch=record.get('batch', ''), **material_details(record))]


def signature(rows):
    return json.dumps(sorted(rows, key=lambda row: row['sku']), ensure_ascii=False, sort_keys=True)


def set_contents(record, rows):
    rows = copy.deepcopy(rows)
    total = sum(row['quantity'] for row in rows)
    if len(rows) > 50 or total > 1000000:
        raise WmsError('每箱最多 50 种物料，箱内总数量不能超过 1000000')
    record.update(contents=rows, container_type='MATERIAL' if rows else 'EMPTY_BIN',
                  sku=rows[0]['sku'] if rows else '', quantity=total,
                  batch=rows[0].get('batch', '') if rows else '', **material_details(rows[0] if rows else {}))


def matches_order(box, order):
    if 'before_contents' in order:
        return signature(contents(box)) == signature(order['before_contents'])
    return box['quantity'] == order['before_quantity'] and box['sku'] == order['original_sku']
