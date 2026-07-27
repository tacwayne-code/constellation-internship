import paramiko
HOST='192.168.31.100'
USER='sameng'
PWD='<YOUR_PASSWORD>'
REMOTE='/home/sameng/production-dashboard-tv'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=12)

def run(cmd):
    sin, sout, serr = c.exec_command(cmd)
    return sout.read().decode('utf-8', 'replace')

with c.open_sftp() as s:
    s.put('tv.html', f'{REMOTE}/tv.html')
print('tv.html uploaded', flush=True)

run('lsof -ti:8090 | xargs kill -9 2>/dev/null || true')
run('sleep 2')

start = (
    f'cd {REMOTE} && set -a && . {REMOTE}/dashboard.env && set +a && '
    f'nohup python3 -u {REMOTE}/server.py > {REMOTE}/server.log 2>&1 < /dev/null & '
    f'echo $! > {REMOTE}/server.pid; sleep 3'
)
run(start)
print('health:', run('curl -fsS http://127.0.0.1:8090/api/health'), flush=True)
print('tv.html status:', run('curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/tv.html'), flush=True)

c.close()