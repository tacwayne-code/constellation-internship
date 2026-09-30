"""One-shot S7 identification and DB2 status read; never writes PLC memory."""
import argparse
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc.s7_physical import read_cpu_state

from snap7.client import Client
from snap7.type import Parameter

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', required=True)
parser.add_argument('--rack', type=int, default=0)
parser.add_argument('--slot', type=int, default=1)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'host': args.host,
          'rack': args.rack, 'slot': args.slot, 'port': 102, 'read_only': True,
          'connected': False, 'errors': {}}
client = Client(auto_reconnect=False)
client.set_param(Parameter.RecvTimeout, 3000)
client.set_param(Parameter.SendTimeout, 3000)
try:
    client.connect(args.host, args.rack, args.slot, tcp_port=102)
    result['connected'] = client.get_connected()
    for name, operation in [('cpu_info', client.get_cpu_info),
                            ('order_code', client.get_order_code),
                            ('cpu_state', lambda: read_cpu_state(client))]:
        try:
            value = operation()
            if hasattr(value, '_fields_'):
                value = {field[0]: getattr(value, field[0]) for field in value._fields_}
            result[name] = value
        except Exception as error:
            result['errors'][name] = str(error)
    try:
        raw = client.db_read(2, 0, 8)
        values = struct.unpack('>4h', raw)
        result['db2'] = dict(zip(['status_dbw0', 'completed_task_id_dbw2',
                                  'alarm_dbw4', 'exchange_status_dbw6'], values))
        result['db2_raw_hex'] = raw.hex()
    except Exception as error:
        result['errors']['db2_read'] = str(error)
except Exception as error:
    result['errors']['connection'] = str(error)
finally:
    client.disconnect()
    client.destroy()

def serialize(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').rstrip('\x00')
    if hasattr(value, '__dict__'):
        return vars(value)
    return str(value)

text = json.dumps(result, ensure_ascii=False, indent=2, default=serialize)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(text, encoding='utf-8')
print(text)
raise SystemExit(0 if result['connected'] and not result['errors'] else 1)
