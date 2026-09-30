"""Read-only environment inventory. JSON output is suitable for issue attachments."""
import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def siemens_installations():
    found = []
    if os.name != 'nt':
        return found
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as item:
                            name = winreg.QueryValueEx(item, 'DisplayName')[0]
                            if any(word in name.upper() for word in ('TIA PORTAL', 'PLCSIM', 'SIMATIC STEP')):
                                found.append(name)
                    except OSError:
                        pass
        except OSError:
            pass
    return sorted(set(found))


def collect(live=False):
    checks = []
    def add(name, ready, detail, required=True):
        checks.append(dict(name=name, status='ready' if ready else ('missing' if required else 'pending'), detail=detail, required=required))
    add('Python', sys.version_info >= (3, 12), sys.version.split()[0])
    for name in ('fastapi', 'uvicorn', 'python-snap7', 'pytest', 'httpx'):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        add(name, version is not None, version or 'Run scripts/setup.ps1')
    add('Frontend build', (ROOT/'web/dist/index.html').exists(), 'web/dist/index.html')
    add('S7 simulator source', (ROOT/'plc/runtime.py').exists(), 'plc/runtime.py')
    add('PLC profile', (ROOT/'config/plc.json').exists(), 'config/plc.json; loopback only')
    add('CAD mesh', (ROOT/'assets/structure/sample-room.glb').exists(), 'assets/structure/sample-room.glb')
    mapping_path = ROOT/'assets/structure/twin-map.json'
    add('CAD mapping', mapping_path.exists(), str(mapping_path.relative_to(ROOT)))
    for name in ('node', 'pnpm', 'git'):
        location = shutil.which(name)
        add(name, bool(location), location or 'Not found on PATH', required=False)
    siemens = siemens_installations()
    add('Siemens software installation (not execution validation)', any('PLCSIM' in name.upper() for name in siemens) and any('PORTAL' in name.upper() or 'STEP' in name.upper() for name in siemens), siemens or 'TIA Portal / PLCSIM not found in installed-program registry.', required=False)
    add('Original Siemens program validation', False, 'Original project has NOT been executed. See docs/西门子环境接入.md for engineering access and virtual CPU diagnostics.', required=False)
    add('Odoo integration', False, 'No Odoo test instance/version configured; outbox simulation only.', required=False)
    add('Field calibration', False, 'CAD kinematic and location bindings are inferred, not field accepted.', required=False)
    health = None
    if live:
        try:
            port = int(os.environ.get('WCS_PORT', '8765'))
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=3) as response:
                health = json.load(response)
            add('WCS live endpoint', health.get('status') == 'ok', health)
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/plc/diagnostics', timeout=3) as response:
                diagnostics = json.load(response)
            add('PLC S7 connection', diagnostics.get('connected') is True, {k:v for k,v in diagnostics.items() if k in ('mode','connected','connection','runtime','cpu','scan','endpoint')})
        except Exception as error:
            add('Live services', False, str(error))
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), root=str(ROOT), platform=platform.platform(), checks=checks, local_lab_ready=all(c['status']=='ready' for c in checks if c['required']), full_original_plc_validation_ready=False, health=health)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = collect(args.live)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report['checks']:
            print(f"[{check['status'].upper():7}] {check['name']}: {check['detail']}")
        print('Local lab ready:', report['local_lab_ready'])
        print('Original Siemens program validation: NOT READY')
    sys.exit(0 if report['local_lab_ready'] else 1)
