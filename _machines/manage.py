#!/usr/bin/env python3
"""Bootstrap telemetry through SSH. Passwords and deployment tokens are entered hidden."""
import argparse
import base64
import concurrent.futures
import getpass
import json
import os
import pathlib
import shlex
import subprocess
import sys

BASE = pathlib.Path(__file__).parent
HOSTS = ['10.20.6.38', '10.20.5.8', '10.20.5.73', '10.20.5.100', '10.20.5.51', '10.20.6.135', '10.20.1.21']
DRIVER = r'''
log_user 0
set timeout 40
set transcript ""
set auth 0
set sudoauth 0
spawn -noecho /usr/bin/ssh -T -o ConnectTimeout=8 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 -o StrictHostKeyChecking=yes -o NumberOfPasswordPrompts=1 -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o LogLevel=ERROR -l wkli $env(MONITOR_HOST) $env(MONITOR_COMMAND)
expect {
  -re {(?i)password: ?$} {
    append transcript $expect_out(buffer)
    incr auth
    if {$auth > 1} {puts "Authentication rejected"; exit 71}
    send -- "$env(MONITOR_PASSWORD)\n"
    exp_continue
  }
  -exact {__MONITOR_SUDO__} {
    append transcript $expect_out(buffer)
    incr sudoauth
    if {$sudoauth > 1} {puts "Sudo rejected"; exit 72}
    send -- "$env(MONITOR_PASSWORD)\n"
    exp_continue
  }
  -exact {__MONITOR_DEPLOY_READY__} {
    append transcript $expect_out(buffer)
    send -- "$env(MONITOR_DEPLOYMENT)\n"
    exp_continue
  }
  timeout {
    append transcript $expect_out(buffer)
    set transcript [string map [list $env(MONITOR_PASSWORD) {<redacted>} $env(MONITOR_DEPLOYMENT) {<redacted>}] $transcript]
    puts "$transcript\nSSH timeout"
    exit 74
  }
  eof {
    append transcript $expect_out(buffer)
    set transcript [string map [list $env(MONITOR_PASSWORD) {<redacted>} $env(MONITOR_DEPLOYMENT) {<redacted>}] $transcript]
    puts $transcript
    set result [wait]
    exit [lindex $result 3]
  }
}
'''


def ssh(host, password, command, deployment=None):
    encoded = base64.b64encode(json.dumps(deployment).encode()).decode() if deployment else '__unused_configuration__'
    env = dict(os.environ, MONITOR_HOST=host, MONITOR_PASSWORD=password, MONITOR_COMMAND=command, MONITOR_DEPLOYMENT=encoded)
    process = subprocess.run(['/usr/bin/expect', '-c', DRIVER], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=60)
    output = process.stdout.replace(password, '<redacted>').replace(encoded, '<redacted>')
    return {'ip': host, 'exit_code': process.returncode, 'output': output.strip()}


def source_command(source):
    encoded = base64.b64encode(source.encode()).decode()
    return "import base64;exec(compile(base64.b64decode(" + repr(encoded) + "),'<monitor>','exec'))"


def install_source():
    agent = base64.b64encode((BASE / 'agent.py').read_bytes()).decode()
    service = base64.b64encode((BASE / 'machines-agent.service').read_bytes()).decode()
    return r'''
import base64, json, os, pathlib, pwd, re, subprocess, sys, tempfile
assert os.geteuid() == 0
print('__MONITOR_DEPLOY_READY__', flush=True)
config = json.loads(base64.b64decode(sys.stdin.readline().strip()))
assert re.fullmatch(r'10\.20\.\d{1,3}\.\d{1,3}', config['machine_id'])
assert re.fullmatch(r'[a-f0-9]{64}', config['token'])
assert config['ingest_url'].startswith('https://') and config['ingest_url'].endswith('/api/ingest')
assert all('\n' not in value and '\r' not in value for value in config.values())
directory = pathlib.Path('/opt/machines-monitor')
agent_path = directory / 'agent.py'
unit = pathlib.Path('/etc/systemd/system/machines-agent.service')
if agent_path.exists() and b'Read-only Linux resource sampling' not in agent_path.read_bytes():
    raise RuntimeError('Existing unrelated agent; leaving it unchanged')
if unit.exists() and 'RUC-Nova read-only machine telemetry' not in unit.read_text():
    raise RuntimeError('Existing unrelated service; leaving it unchanged')
try:
    account = pwd.getpwnam('machines-monitor')
    assert account.pw_uid != 0 and account.pw_shell.endswith('nologin')
except KeyError:
    subprocess.run(['useradd','--system','--user-group','--no-create-home','--shell','/usr/sbin/nologin','machines-monitor'],check=True)
directory.mkdir(mode=0o755, exist_ok=True)
assert directory.stat().st_uid == 0
def atomic(path, data, mode):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):os.unlink(name)
atomic(agent_path, base64.b64decode('__AGENT_BASE64__'), 0o644)
atomic(unit, base64.b64decode('__SERVICE_BASE64__'), 0o644)
environment = '\n'.join([
    'MACHINE_ID='+config['machine_id'],
    'MACHINES_INGEST_URL='+config['ingest_url'],
    'MACHINES_AGENT_TOKEN='+config['token'],
    'MACHINES_INTERVAL=30',
])+'\n'
atomic(pathlib.Path('/etc/machines-monitor.env'),environment.encode(),0o600)
sample = subprocess.run(['runuser','-u','machines-monitor','--','/usr/bin/python3',str(agent_path),'--once','--machine-id',config['machine_id']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20,check=True)
data=json.loads(sample.stdout)
subprocess.run(['systemctl','daemon-reload'],check=True)
active=subprocess.run(['systemctl','is-active','--quiet','machines-agent.service']).returncode==0
subprocess.run(['systemctl','enable','machines-agent.service'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
subprocess.run(['systemctl','restart' if active else 'start','machines-agent.service'],check=True)
subprocess.run(['systemctl','is-active','--quiet','machines-agent.service'],check=True)
print(json.dumps({'installed':True,'machine_id':config['machine_id'],'gpus':len(data['gpus']),'gpu_error':data['gpu_error']}))
'''.replace('__AGENT_BASE64__', agent).replace('__SERVICE_BASE64__', service)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['sample','install','health'])
    parser.add_argument('--host', choices=HOSTS)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    parser.add_argument('--stdin-config', action='store_true', help='Read automation configuration from a pipe')
    args = parser.parse_args()
    if args.stdin_config:
        if sys.stdin.isatty():
            parser.error('automation configuration requires a pipe')
        private_config = json.loads(sys.stdin.readline())
        password = private_config['password']
        deployments = private_config.get('deployments', {})
    else:
        password = getpass.getpass('SSH password (hidden): ')
        deployments = {}
    if args.action == 'install':
        if not args.stdin_config:
            deployments = json.loads(getpass.getpass('Deployment JSON (hidden): '))
        assert set(deployments).issubset(HOSTS)
    results = []
    selected_hosts = [args.host] if args.host else HOSTS
    if args.action == 'health':
        source = r'''
import importlib.util, os, subprocess, urllib.error
subprocess.run(['systemctl','is-active','machines-agent.service'])
subprocess.run(['journalctl','-u','machines-agent.service','--no-pager','-n','8','-o','cat'])
subprocess.run(['curl','--silent','--show-error','--connect-timeout','3','--max-time','7',
 '-A','RUC-Nova-Machines/1.0','-I','https://ruc-nova-machines-status.classyjay7.chatgpt.site/api/status'])
for line in open('/etc/machines-monitor.env'):
 key,value=line.rstrip('\n').split('=',1)
 os.environ[key]=value
spec=importlib.util.spec_from_file_location('agent','/opt/machines-monitor/agent.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
try:
 agent.upload(agent.snapshot(os.environ['MACHINE_ID']),os.environ['MACHINES_INGEST_URL'],os.environ['MACHINES_AGENT_TOKEN'])
 print('REPORT_OK')
except urllib.error.HTTPError as error:
 print('HTTP_ERROR',error.code,error.read(500).decode(errors='replace'))
except Exception as error:
 print(type(error).__name__,str(error))
'''
        command='sudo -k -S -p __MONITOR_SUDO__ -- /usr/bin/python3 -u -c '+shlex.quote(source_command(source))
        for host in selected_hosts:
            result=ssh(host,password,command)
            results.append(result)
            args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps(result,ensure_ascii=False),flush=True)
    elif args.action == 'sample':
        source = source_command((BASE / 'agent.py').read_text())
        def sample(host):
            command = '/usr/bin/python3 -c ' + shlex.quote(source) + ' --once --machine-id ' + shlex.quote(host)
            result = ssh(host,password,command)
            for line in result['output'].splitlines():
                if line.startswith('{'):
                    result['telemetry'] = json.loads(line)
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(sample, selected_hosts))
        args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
        for result in results:
            data=result.get('telemetry',{})
            print(json.dumps({'ip':result['ip'],'exit_code':result['exit_code'],'gpus':len(data.get('gpus',[])),
                'models':[gpu['name'] for gpu in data.get('gpus',[])],'gpu_error':data.get('gpu_error')}),flush=True)
    else:
        command='sudo -k -S -p __MONITOR_SUDO__ -- /usr/bin/python3 -c '+shlex.quote(source_command(install_source()))
        for host, deployment in deployments.items():
            try:result=ssh(host,password,command,deployment)
            except Exception as exc:result={'ip':host,'error':type(exc).__name__}
            results.append(result)
            args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps(result,ensure_ascii=False),flush=True)
    password = None


if __name__ == '__main__':
    main()
