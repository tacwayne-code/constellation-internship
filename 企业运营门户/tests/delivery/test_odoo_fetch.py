import pytest

from delivery.odoo_fetch import fetch_orders, ORDER_FIELDS
from lighthouse.odoo import OdooError


class Client:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, *args):
        self.calls.append(args)
        return self.result


def test_bounded_read_only_purchase_query():
    client = Client([{'id': 1, 'name': 'PO001'}])
    assert fetch_orders(client, [('id', '=', 1)], 1) == client.result
    assert client.calls == [('purchase.order', 'search_read', [[('id', '=', 1)]],
                             {'fields': ORDER_FIELDS, 'limit': 1, 'order': 'id desc'})]
    with pytest.raises(ValueError):
        fetch_orders(client, [], 100)
    assert len(client.calls) == 1


@pytest.mark.parametrize('bad', [None, {}, [None], [{'name': 'PO001'}], [{'id': 1, 'name': False}]])
def test_invalid_response_rejected(bad):
    with pytest.raises(OdooError):
        fetch_orders(Client(bad), [])
