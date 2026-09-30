"""Build a relocatable Windows x64 physical-PLC installer, without live data."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / 'scripts/windows'


def write_cmd(path, lines):
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('ascii'))


def main():
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        raise SystemExit('Build on 64-bit Windows with the locked project environment.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    build = ROOT / '.build-installer' / stamp
    build.mkdir(parents=True)
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True)
    name = f'WCS-PLC-Commissioning-Windows-x64-{stamp}'
    package = releases / name
    package.mkdir()
    tooling = ROOT / '.build-installer/tooling'
    if not (tooling / 'PyInstaller').is_dir():
        raise SystemExit('Install pyinstaller==6.22.3 using pip --target .build-installer/tooling first.')
    env = dict(os.environ, PYTHONPATH=str(tooling), PYTHONUTF8='1')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
               '--name', 'WcsPlcConsole', '--paths', str(ROOT),
               '--distpath', str(build / 'dist'), '--workpath', str(build / 'work'),
               '--specpath', str(build), '--hidden-import', 'backend.physical_app',
               '--collect-submodules', 'uvicorn', '--copy-metadata', 'python-snap7',
               '--copy-metadata', 'fastapi', '--copy-metadata', 'uvicorn',
               '--exclude-module', 'pytest', '--exclude-module', 'tkinter',
               '--exclude-module', 'IPython', str(WINDOWS / 'entry.py')]
    print('Building standalone runtime...', flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    payload = package / 'payload'
    shutil.copytree(build / 'dist/WcsPlcConsole', payload)
    (payload / 'web').mkdir()
    shutil.copy2(ROOT / 'web/physical.html', payload / 'web/physical.html')
    (payload / 'tools').mkdir()
    for file in ['install.ps1', 'Install.Core.ps1']:
        (package / file).write_text((WINDOWS / file).read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    for file in ['firewall.ps1', 'open-console.ps1']:
        (payload / 'tools' / file).write_text((WINDOWS / file).read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    for directory in [package, payload]:
        shutil.copy2(WINDOWS / 'README.txt', directory / '安装与使用说明.txt')
    write_cmd(package / 'Install.cmd', ['@echo off', 'cd /d "%~dp0"',
              'powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0install.ps1"',
              'if errorlevel 1 pause'])
    shutil.copy2(package / 'Install.cmd', package / '一键安装.cmd')
    write_cmd(payload / 'StartBackend.cmd', ['@echo off', 'cd /d "%~dp0"', 'WcsPlcConsole.exe', 'pause'])
    write_cmd(payload / 'CheckEnvironment.cmd', ['@echo off', 'cd /d "%~dp0"',
              'WcsPlcConsole.exe --self-test', 'if errorlevel 1 goto end', 'WcsPlcConsole.exe --check', ':end', 'pause'])
    write_cmd(payload / 'OpenConsole.cmd', ['@echo off', r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\open-console.ps1"', 'if errorlevel 1 pause'])
    write_cmd(payload / 'AllowLan.cmd', ['@echo off', r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\firewall.ps1"',
              'if errorlevel 1 (echo LAN firewall setup failed or cancelled. See firewall-setup-error.txt.) else (echo LAN firewall rule is ready.)', 'pause'])
    for english, chinese in [('StartBackend.cmd', '启动后台.cmd'), ('OpenConsole.cmd', '打开调试台.cmd'),
                             ('CheckEnvironment.cmd', '环境自检.cmd'), ('AllowLan.cmd', '允许局域网访问.cmd')]:
        shutil.copy2(payload / english, payload / chinese)
    source = payload / 'source'
    files = ['run.py', 'requirements.lock.txt', 'requirements.txt', 'backend/__init__.py',
             'backend/physical_app.py', 'backend/physical_control.py', 'plc/__init__.py', 'plc/s7_physical.py',
             'tests/test_physical_control.py', 'tests/test_physical_app.py', 'tests/test_s7_physical.py',
             'tests/physical_connection_check.cjs', 'tests/test_windows_distribution.py', 'scripts/package-windows.py', 'web/physical.html']
    files.extend(p.relative_to(ROOT).as_posix() for p in WINDOWS.glob('*') if p.is_file())
    for file in files:
        destination = source / file
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / file, destination)
    license_root = payload / 'licenses'
    license_root.mkdir()
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', license_root / 'Python-LICENSE.txt')
    inventory = []
    names = [line.split('==')[0] for line in (ROOT / 'requirements.lock.txt').read_text().splitlines() if '==' in line]
    distributions = [importlib.metadata.distribution(name) for name in names]
    distributions.extend(importlib.metadata.distributions(path=[str(tooling)]))
    for dist in distributions:
        label = f'{dist.metadata["Name"]}-{dist.version}'
        inventory.append(dict(name=dist.metadata['Name'], version=dist.version,
                              license=dist.metadata.get('License-Expression') or dist.metadata.get('License', ''),
                              homepage=dist.metadata.get('Home-page', '')))
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ['license', 'copying', 'notice']) and str(file).endswith(('.py', '.pyc', '.pyd', '.exe')) is False:
                original = Path(dist.locate_file(file))
                if original.is_file():
                    dest = license_root / label / str(file).replace('..', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, dest)
    (license_root / 'DEPENDENCIES.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    import snap7
    shutil.copytree(Path(snap7.__file__).parent, payload / 'third-party-source/snap7', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (payload / 'BUILD-INFO.json').write_text(json.dumps(dict(built_at=datetime.now().isoformat(),
        python=sys.version, platform=sys.platform, version=name, runtime='PyInstaller 6.22.3',
        live_data_included=False, dependencies=inventory), ensure_ascii=False, indent=2), encoding='utf-8')
    subprocess.run([str(payload / 'WcsPlcConsole.exe'), '--self-test'], cwd=payload, check=True)
    manifest = []
    for file in sorted(payload.rglob('*')):
        if file.is_file():
            manifest.append(dict(path=file.relative_to(payload).as_posix(), size=file.stat().st_size,
                                 sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    (package / 'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = releases / (name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(package.rglob('*')):
            if file.is_file():
                z.write(file, Path(name) / file.relative_to(package))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for row in manifest:
            assert hashlib.sha256(z.read(f'{name}/payload/{row["path"]}')).hexdigest() == row['sha256']
        assert not any('/data/' in file or file.endswith('physical-control-key.txt') or '.sqlite3' in file for file in z.namelist())
    with archive.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='utf-8')
    result = dict(archive=str(archive), package=str(package), size=archive.stat().st_size, files=len(manifest), sha256=digest)
    (build / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
