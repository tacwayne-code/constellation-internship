"""Read physical CPU status with the standard S7 SZL request.

python-snap7 3.1.2 get_cpu_state() builds a nonstandard one-byte ReadVar
request, rejected by this CPU with 0x84/0x04. Use SZL 0x0424 instead.
Reference: Snap7 TSnap7MicroClient::opGetPlcStatus, opData[7]:
https://github.com/SCADACS/snap7/blob/master/src/core/s7_micro_client.cpp
The locked pure-Python read_szl() retains the four-byte record header in Data.
"""
import struct


def read_cpu_state(client):
    szl = client.read_szl(0x0424, 0)
    size = int(szl.Header.LengthDR)
    raw = bytes(szl.Data)[:size]
    if len(raw) < 8:
        raise ValueError('CPU status SZL 0x0424 response is truncated')
    record_length, count = struct.unpack('>HH', raw[:4])
    if record_length < 4 or count != 1 or len(raw) != 4 + record_length:
        raise ValueError('CPU status SZL 0x0424 record layout is invalid')
    return {0: 'S7CpuStatusUnknown', 4: 'S7CpuStatusStop', 8: 'S7CpuStatusRun'}.get(
        raw[7], 'S7CpuStatusUnknown')
