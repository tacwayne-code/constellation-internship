import json
from datetime import datetime, timedelta, timezone
from backend.native_plc import snapshot

def test_native_telemetry_never_treats_old_or_missing_samples_as_live(tmp_path):
    path = tmp_path / 'sample.json'
    assert snapshot(path)['live'] is False
    for seconds, expected in [(1, True), (30, False), (-30, False)]:
        path.write_text(json.dumps({'updated_at': (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(),
                                    'state': 'Run', 'signals': [{'name': 'test', 'value': 42}]}))
        result = snapshot(path)
        assert result['live'] is expected
        assert result['signals'][0]['value'] == 42
        assert result['task_dispatch_enabled'] is False
    path.write_text('{broken')
    assert snapshot(path)['live'] is False
