"""Deterministic allocation preferences; eligibility/interlocks stay in the store."""
DEFAULT_POLICY = dict(priority='LEVEL_COLUMN', side_order='LEFT_FIRST',
                      level_order='LOW_FIRST', column_order='FRONT_FIRST')
PRIORITIES = {
    'LEVEL_COLUMN': ('level', 'column'),
    'COLUMN_LEVEL': ('column', 'level'),
}
LEGACY_PRIORITIES = {key: 'LEVEL_COLUMN' if key.index('LEVEL') < key.index('COLUMN') else 'COLUMN_LEVEL'
                     for key in ('SIDE_LEVEL_COLUMN', 'SIDE_COLUMN_LEVEL', 'LEVEL_SIDE_COLUMN',
                                 'LEVEL_COLUMN_SIDE', 'COLUMN_SIDE_LEVEL', 'COLUMN_LEVEL_SIDE')}


def allocation_policy(state):
    policy = {**DEFAULT_POLICY, 'revision': 0, **state.get('allocation_policy', {})}
    policy['priority'] = LEGACY_PRIORITIES.get(policy['priority'], policy['priority'])
    return policy


def validate_policy(policy):
    choices = dict(priority=set(PRIORITIES) | set(LEGACY_PRIORITIES), side_order={'LEFT_FIRST', 'RIGHT_FIRST', 'BALANCED'},
                   level_order={'LOW_FIRST', 'HIGH_FIRST'}, column_order={'FRONT_FIRST', 'BACK_FIRST'})
    if set(policy) != set(DEFAULT_POLICY) or any(policy[key] not in values for key, values in choices.items()):
        raise ValueError('库位分配规则无效，请重新选择方向及优先级')
    return {**policy, 'priority': LEGACY_PRIORITIES.get(policy['priority'], policy['priority'])}


def record_allocation(state, location):
    """Advance only for a committed automatic selection, in the ledger transaction."""
    if allocation_policy(state)['side_order'] == 'BALANCED':
        state['allocation_next_side'] = 3 - location['side']


def allocation_key(state, location, inbound=True):
    policy = allocation_policy(state)
    preferred_side = state.get('allocation_next_side', 1) if policy['side_order'] == 'BALANCED' else (
        1 if policy['side_order'] == 'LEFT_FIRST' else 2)
    values = dict(level=location['level'] if policy['level_order'] == 'LOW_FIRST' else -location['level'],
                  column=location['column'] if policy['column_order'] == 'FRONT_FIRST' else -location['column'])
    # The preference only ranks eligible candidates. Double-deep inbound still
    # fills an accessible rear before its front; fetch keeps the original tie-break.
    # Side selection always precedes level/column ranking: exhaust that side's
    # eligible candidates before falling back, even when migrating a v1.4 rule.
    return (location['side'] != preferred_side,) + tuple(values[key] for key in PRIORITIES[policy['priority']]) + (
        -location['depth'] if inbound else location['depth'], location['id'])
