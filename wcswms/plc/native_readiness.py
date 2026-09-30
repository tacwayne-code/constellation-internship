"""Conservative read-only commissioning checks for the original program.

Passing these checks is not permission to dispatch a task: drive simulation and
the original handshake require separate validation.
"""
import math

AXES = ('行走轴', '升降轴', '货叉轴')
INTERLOCKS = ('急停按下', '行走超前限', '行走超后限', '升降超上限', '升降超下限',
              '货叉超左限', '货叉超右限')


def assess(signals, live, state):
    values = {r['name']: r.get('value') for r in signals if not r.get('error')}
    blockers = []
    def issue(code, detail):
        blockers.append({'code': code, 'detail': detail})
    def number(name):
        value = values.get(name)
        return value if type(value) in (int, float) and math.isfinite(value) else None
    if not live:
        issue('TELEMETRY_UNAVAILABLE', '采集离线或过期，不能判断设备就绪')
        return {'ready': False, 'baseline_ready': False, 'blockers': blockers}
    if state != 'Run':
        issue('CPU_NOT_RUN', '原程序 CPU 未处于 RUN')
    for name in INTERLOCKS:
        if values.get(name) is True:
            issue('INTERLOCK_ACTIVE', name)
        elif values.get(name) is not False:
            issue('SIGNAL_UNKNOWN', name + ' 未取得有效布尔反馈')
    for axis in AXES:
        prefix = 'HMI保持.' + axis + '参数.'
        for field in ('工作速度', '加速度', '减速度'):
            value = number(prefix + field)
            if value is None or value <= 0:
                issue('MOTION_PARAMETER_INVALID', prefix + field + ' 未初始化或无有效正值')
        lower = number(prefix + ('行走最小值' if axis == '行走轴' else '行程最小值'))
        upper = number(prefix + '行程最大值')
        if lower is None or upper is None or upper <= lower:
            issue('TRAVEL_RANGE_INVALID', axis + ' 行程范围无效或未采集')
    laser = number('HMI不保持.激光位置')
    if laser is None or laser <= 0:
        issue('LASER_INVALID', '激光反馈未取得有效正值（原程序报警 24 条件）')
    code = number('交互.PLC-上位机.异常代码')
    if code is None or code != 0:
        issue('PLC_ALARM', '原程序异常代码：' + str(code))
    status = number('交互.PLC-上位机.状态')
    if status != 8:
        issue('PLC_NOT_IDLE', '基本初态要求原程序状态 8（空闲），当前：' + str(status))
    baseline_ready = not blockers
    issue('NATIVE_HANDSHAKE_PENDING', '原程序任务握手与三轴闭环尚未验收')
    return {'ready': False, 'baseline_ready': baseline_ready, 'blockers': blockers}
