"""Read-only presentation of the Siemens API sampler; never emulates PLC data."""
import json
from datetime import datetime, timezone
from pathlib import Path
from plc.native_readiness import assess

ROOT = Path(__file__).resolve().parents[1]

def snapshot(path=None):
    source = Path(path) if path else ROOT / 'data/siemens/native-telemetry.json'
    try:
        data = json.loads(source.read_text(encoding='utf-8-sig'))
        sampled = datetime.fromisoformat(data['updated_at'])
        age = (datetime.now(timezone.utc) - sampled).total_seconds()
        data['age_seconds'] = round(age, 2)
        data['live'] = 0 <= age < 5
        data['task_dispatch_enabled'] = False
        data['plant_feedback_simulated'] = False
        data['readiness'] = assess(data.get('signals', []), data['live'], data.get('state'))
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {'live': False, 'state': 'Unavailable', 'signals': [],
                'task_dispatch_enabled': False, 'plant_feedback_simulated': False,
                'detail': '原生 PLC 采集尚未启动或数据不可读'}
