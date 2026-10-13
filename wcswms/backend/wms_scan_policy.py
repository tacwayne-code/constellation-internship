"""Per-device outbound material-order verification, stored with the ledger."""
from .wms_store import WmsError


def scan_policy(state):
    return {'require_barcode': True, 'require_sku': True, 'revision': 0,
            **state.get('outbound_scan_policy', {})}


def configure(store, request):
    def apply(state):
        current = scan_policy(state)
        if request['expected_revision'] != current['revision']:
            raise WmsError('出库扫码设置已被其他终端修改，请重新读取后保存')
        values = {key: request[key] for key in ('require_barcode', 'require_sku')}
        if any(type(value) is not bool for value in values.values()):
            raise WmsError('扫码选项必须为开关值')
        if any(current[key] != value for key, value in values.items()):
            state['outbound_scan_policy'] = {**values, 'revision': current['revision'] + 1}
            store.event(state, '出库扫码设置：箱号' + ('必扫' if values['require_barcode'] else '免扫')
                        + '，物料编码' + ('必扫' if values['require_sku'] else '免扫'))
        return scan_policy(state)
    return store.once(request['request_id'], {'operation': 'outbound-scan-policy', **request}, apply)
