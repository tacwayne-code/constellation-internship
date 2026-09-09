import copy
import threading
import time
import logging
from datetime import datetime, timezone


class SnapshotStore:
    def __init__(self, configured, refresh):
        self.configured = configured
        self.refresh = refresh
        self.lock = threading.RLock()
        self.entries = {}

    def collect(self, key, loader):
        try:
            data, issues = loader()
            with self.lock:
                self.entries[key] = {'data': data, 'issues': sorted(issues), 'error': None,
                    'at': datetime.now(timezone.utc).isoformat(), 'mono': time.monotonic()}
        except Exception as exc:
            logging.getLogger('lighthouse.collector').warning('Snapshot %s failed (%s)', key, type(exc).__name__)
            with self.lock:
                entry = self.entries.setdefault(key, {})
                entry['error'] = '本轮读取失败，请检查 Odoo 连接、账号权限及字段配置'

    def get(self, key):
        with self.lock:
            entry = copy.deepcopy(self.entries.get(key, {}))
        data = entry.get('data')
        age = max(0, int(time.monotonic() - entry['mono'])) if 'mono' in entry else None
        status = 'not_configured' if not self.configured else 'loading'
        if data is not None:
            status = 'partial' if entry.get('issues') else 'live'
            if entry.get('error') or age > self.refresh * 2:
                status = 'stale'
        elif entry.get('error'):
            status = 'error'
        if data is not None:
            data.setdefault('meta', {})['updatedAt'] = entry.get('at')
        return {'ok': data is not None, 'data': data, 'status': status,
                'updatedAt': entry.get('at'), 'ageSeconds': age,
                'refreshSeconds': self.refresh, 'issues': entry.get('issues', []), 'error': entry.get('error')}
