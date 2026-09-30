"""Exercise the real Windows installer in temporary directories, with no PLC access."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    temp_parent = Path(tempfile.gettempdir()).resolve()
    work = Path(tempfile.mkdtemp(prefix='wcs-installer-validation-', dir=temp_parent)).resolve()
    assert work.parent == temp_parent and work.name.startswith('wcs-installer-validation-')
    env = {key: value for key, value in os.environ.items() if key.upper() not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PSMODULEPATH')}
    env['PATH'] = os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot'] + '\\System32\\WindowsPowerShell\\v1.0;' + os.environ['SystemRoot']
    powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')

    def run(argv, success=True):
        result = subprocess.run(argv, env=env, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        if (result.returncode == 0) != success:
            raise AssertionError(result.stdout.decode('utf-8', errors='replace'))
        return result.stdout

    def install(target, *extra, success=True):
        return run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(package / 'install.ps1'),
                    '-NonInteractive', '-TargetPath', str(target), '-ListenHost', '127.0.0.1',
                    '-PlcHost', '192.0.2.100', '-WebPort', '18876', *extra], success=success)

    try:
        run([powershell, '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
             str(package / 'install.ps1'), '-ValidateUi'])
        target = work / '新后台 安装测试'
        history = work / 'physical-commands.sqlite3'
        with closing(sqlite3.connect(history)) as db:
            db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL, task_id INTEGER)')
            db.execute('INSERT INTO commands VALUES (?,?,?,?)', ('fixture-request', '{}', '{"status":"UNCERTAIN"}', 321))
            db.commit()
        install(target, '-HistoryPath', str(history))
        exe = str(target / 'WcsPlcConsole.exe')
        run([exe, '--self-test'])
        run([exe, '--check'])
        run([exe, '--smoke-test'])
        config_path = target / 'config/plc.json'
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        assert config['listen_host'] == '127.0.0.1' and config['web_port'] == 18876
        with closing(sqlite3.connect(target / 'data/physical-commands.sqlite3')) as db:
            assert db.execute('SELECT task_id FROM commands').fetchone() == (321,)
        key_path = target / 'data/physical-control-key.txt'
        key = key_path.read_bytes()
        assert len(key) > 10
        install(target)
        assert key_path.read_bytes() == key
        assert json.loads(config_path.read_text(encoding='utf-8-sig')) == config
        with closing(sqlite3.connect(target / 'data/physical-commands.sqlite3')) as db:
            assert db.execute('SELECT task_id FROM commands').fetchone() == (321,)
        assert len(list(work.glob(target.name + '.backup-*'))) == 1
        unrelated = work / 'other-program'
        unrelated.mkdir()
        (unrelated / 'keep.txt').write_text('preserve me')
        install(unrelated, success=False)
        assert (unrelated / 'keep.txt').read_text() == 'preserve me'
        assert len(list(unrelated.iterdir())) == 1
        manifest_path = package / 'PACKAGE-MANIFEST.json'
        original = manifest_path.read_bytes()
        try:
            manifest = json.loads(original)
            manifest[0]['sha256'] = '0' * 64
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            install(work / 'tampered-package', success=False)
            assert not (work / 'tampered-package').exists()
            manifest[0]['path'] = '../escape.txt'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            install(work / 'bad-manifest', success=False)
            assert not (work / 'bad-manifest').exists()
        finally:
            manifest_path.write_bytes(original)
        # An active backend lock must block an upgrade before replacing any files.
        import msvcrt
        with (target / 'data/server.lock').open('a+b') as handle:
            handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                install(target, success=False)
                assert key_path.read_bytes() == key
            finally:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        # Reject accidental shell escapes in generated launchers (e.g. Python '\f').
        for cmd in target.glob('*.cmd'):
            assert not any(byte < 32 and byte not in (9, 10, 13) for byte in cmd.read_bytes()), cmd.name
        print(json.dumps(dict(status='PASS', checks=['Windows PowerShell 5.1 installer', 'installation form creation', 'fresh install to Unicode path',
            'self-contained runtime with Python removed from PATH', 'bundled HTTP + assets', 'task history import',
            'upgrade retains config/key/history', 'unrelated directory protection', 'manifest hash + traversal rejection',
            'active backend upgrade refusal', 'launcher encoding'], plc_connections=0, plc_writes=0,
            permanent_install=False, firewall_changes=False, desktop_shortcuts=False), ensure_ascii=False, indent=2))
    finally:
        # Delete only the dedicated temporary test tree created and checked above.
        if work.resolve().parent != temp_parent or not work.name.startswith('wcs-installer-validation-'):
            raise RuntimeError('Refusing cleanup outside the test directory.')
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
