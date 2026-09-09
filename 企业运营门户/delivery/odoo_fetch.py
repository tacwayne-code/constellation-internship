"""Bounded, read-only purchase retrieval shared by the app and connection check."""
import json

from lighthouse.odoo import OdooError, ReadOnlyOdoo
from lighthouse.settings import Settings

ORDER_FIELDS = ['id', 'name', 'partner_id', 'state', 'date_planned', 'origin', 'write_date']


def fetch_orders(client, domain, limit=51):
    if not 1 <= limit <= 51:
        raise ValueError('单次采购查询上限为51条')
    rows = client.call('purchase.order', 'search_read', [domain],
                       {'fields': ORDER_FIELDS, 'limit': limit, 'order': 'id desc'})
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get('id'), int)
        or not isinstance(row.get('name'), str) for row in rows
    ):
        raise OdooError('Odoo采购响应格式无效')
    return rows


def main():
    client = None
    try:
        client = ReadOnlyOdoo(Settings.from_env())
        rows = fetch_orders(client, [], limit=1)
        # Do not print credentials, account identity, or order contents.
        print(json.dumps({'ok': True, 'message': 'Odoo采购读取正常',
                          'sample_count': len(rows)}, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({'ok': False, 'message': 'Odoo读取失败，请检查本机配置、网络和账号读取权限'},
                         ensure_ascii=False))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
