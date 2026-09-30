from plc.native_readiness import AXES, INTERLOCKS, assess


def baseline():
    values = {name: False for name in INTERLOCKS}
    for axis in AXES:
        prefix = 'HMI保持.' + axis + '参数.'
        values.update({prefix + key: 10 for key in ('工作速度', '加速度', '减速度')})
        values[prefix + ('行走最小值' if axis == '行走轴' else '行程最小值')] = 0
        values[prefix + '行程最大值'] = 1000
    values.update({'HMI不保持.激光位置': 100, '交互.PLC-上位机.异常代码': 0, '交互.PLC-上位机.状态': 8})
    return [{'name': name, 'value': value} for name, value in values.items()]


def test_valid_parameters_do_not_authorize_unvalidated_handshake():
    result = assess(baseline(), True, 'Run')
    assert not result['ready']
    assert result['baseline_ready']
    assert [b['code'] for b in result['blockers']] == ['NATIVE_HANDSHAKE_PENDING']


def test_missing_stale_and_failed_signals_are_not_treated_as_safe():
    assert assess(baseline(), False, 'Run')['blockers'][0]['code'] == 'TELEMETRY_UNAVAILABLE'
    rows = baseline()
    rows[0]['error'] = 'read failed'
    assert any(b['code'] == 'SIGNAL_UNKNOWN' for b in assess(rows, True, 'Run')['blockers'])
    assert any(b['code'] == 'TRAVEL_RANGE_INVALID' for b in assess([], True, 'Run')['blockers'])


def test_zero_speed_and_active_interlock_are_reported():
    rows = baseline()
    rows[0]['value'] = True
    next(r for r in rows if r['name'].endswith('行走轴参数.工作速度'))['value'] = 0
    codes = {b['code'] for b in assess(rows, True, 'Run')['blockers']}
    assert {'INTERLOCK_ACTIVE', 'MOTION_PARAMETER_INVALID'} <= codes
