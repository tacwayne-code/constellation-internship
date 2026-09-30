from types import SimpleNamespace
import pytest
from plc.s7_physical import read_cpu_state


class Cpu:
    def __init__(self, raw): self.raw = raw
    def read_szl(self, code, index):
        assert (code, index) == (0x0424, 0)
        return SimpleNamespace(Header=SimpleNamespace(LengthDR=len(self.raw)), Data=self.raw)
    def get_cpu_state(self):
        raise AssertionError('Nonstandard CPU status request must not be used')


@pytest.mark.parametrize('value,expected', [(8, 'S7CpuStatusRun'), (4, 'S7CpuStatusStop'),
                                          (0, 'S7CpuStatusUnknown'), (255, 'S7CpuStatusUnknown')])
def test_standard_cpu_status(value, expected):
    # Format captured from this physical CPU, with state varied for regression.
    raw = bytes.fromhex('001400015102ff') + bytes([value]) + bytes(16)
    assert read_cpu_state(Cpu(raw)) == expected


@pytest.mark.parametrize('raw', [b'', bytes(7), bytes.fromhex('001400015102ff08'),
                                bytes.fromhex('000400025102ff08')])
def test_truncated_or_wrong_layout_cannot_report_run(raw):
    with pytest.raises(ValueError):
        read_cpu_state(Cpu(raw))
