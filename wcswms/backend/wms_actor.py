"""Authenticated request identity; never taken from a client supplied payload."""
from contextvars import ContextVar
from functools import wraps

current_actor = ContextVar('wms_current_actor', default=None)


def system_operation(operation):
    """Background workers may inherit the device-creation request's context."""
    @wraps(operation)
    def run(*args, **kwargs):
        token = current_actor.set(None)
        try:
            return operation(*args, **kwargs)
        finally:
            current_actor.reset(token)
    return run


def identity(user):
    return {key: user[key] for key in ('id', 'username', 'display_name')} if user else None


def record_operator(state, task_id, created=False):
    actor = current_actor.get()
    task = next((row for row in state['tasks'] if row['id'] == task_id), None)
    if not actor or not task:
        return
    for row in [task, *[order for order in state.get('orders', []) if order['id'] == task.get('order_id')]]:
        if created and (row is task or task.get('order_leg') == 'FETCH'):
            row.setdefault('created_by', dict(actor))
        row['operator'] = dict(actor)
