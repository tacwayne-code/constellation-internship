"""Read-only Windows loader check; run in a separate process from TIA."""
import ctypes
import json
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--bin', default='D:/Siemens/Automation/Portal V20/Bin')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint]
kernel.LoadLibraryExW.restype = ctypes.c_void_p
results = []
for name in ['MSVCP140.dll', 'MSVCP140_CODECVT_IDS.dll', 'MSVCP140_ATOMIC_WAIT.dll',
             'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
             'Siemens.MP.tkDiagnostics.dll', 'Siemens.MP.tkTIABridge.dll',
             'Siemens.MP.tkImplementation.dll']:
    local = Path(args.bin) / name
    target = str(local) if local.exists() else name
    ctypes.set_last_error(0)
    handle = kernel.LoadLibraryExW(target, None, 0x1100 if local.exists() else 0x1000)
    error = 0 if handle else ctypes.get_last_error()
    results.append({'module': name, 'loaded': bool(handle), 'winerror': error})
report = json.dumps(results, indent=2)
if args.output:
    args.output.write_text(report, encoding='utf-8')
print(report)
raise SystemExit(0 if all(item['loaded'] for item in results) else 1)
