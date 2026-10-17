#!/usr/bin/env python3
"""Root installer for the dedicated collector; private configuration arrives on stdin."""
import base64
import json
import os
import pathlib
import pwd
import re
import subprocess
import sys
import tempfile

MARKER = 'RUC-Nova read-only GPU collector v1\n'
USER = 'machines-collector'
BASE = pathlib.Path('/opt/machines-collector')
STATE = pathlib.Path('/var/lib/machines-collector')
ETC = pathlib.Path('/etc/machines-collector')
UNIT = pathlib.Path('/etc/systemd/system/machines-collector.service')
HOSTS = ('10.20.6.38', '10.20.5.8', '10.20.5.73', '10.20.5.100',
         '10.20.5.51', '10.20.6.135', '10.20.1.21')


def atomic(path, content, mode=0o644):
    if path.is_symlink():
        raise RuntimeError('Unexpected symlink')
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def initialize():
    marker = BASE / '.managed'
    managed = marker.exists() and marker.read_text() == MARKER
    if not managed and (BASE.exists() or STATE.exists() or ETC.exists() or UNIT.exists()):
        raise RuntimeError('An unrelated collector path exists')
    try:
        account = pwd.getpwnam(USER)
        if not managed:
            raise RuntimeError('An unrelated account exists')
        assert account.pw_uid != 0 and account.pw_shell.endswith('nologin')
    except KeyError:
        subprocess.run(['useradd', '--system', '--user-group', '--home-dir', str(STATE),
                        '--no-create-home', '--shell', '/usr/sbin/nologin', USER], check=True)
        account = pwd.getpwnam(USER)
    BASE.mkdir(mode=0o755, exist_ok=True)
    atomic(marker, MARKER.encode())
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    os.chmod(STATE, 0o700)
    key = STATE / 'id_ed25519'
    if not key.exists():
        subprocess.run(['runuser', '-u', USER, '--', 'ssh-keygen', '-q', '-t', 'ed25519',
                        '-N', '', '-C', 'ruc-nova-gpu-collector', '-f', str(key)], check=True)
    public = (STATE / 'id_ed25519.pub').read_text().strip()
    assert public.startswith('ssh-ed25519 ')
    print(json.dumps({'initialized': True, 'public_key': public}), flush=True)


def install(config):
    assert (BASE / '.managed').read_text() == MARKER
    if 'tokens' not in config:
        config['tokens'] = json.loads((ETC / 'config.json').read_text())['tokens']
    assert set(config['tokens']) == set(HOSTS)
    assert all(re.fullmatch(r'[a-f0-9]{64}', token) for token in config['tokens'].values())
    assert config['endpoint'] == 'https://ruc-nova-machines-status.classyjay7.chatgpt.site/api/ingest'
    if UNIT.exists() and 'RUC-Nova read-only GPU collection through SSH gateway' not in UNIT.read_text():
        raise RuntimeError('An unrelated service exists')
    ETC.mkdir(mode=0o755, exist_ok=True)
    for name in ('agent.py', 'collector.py', 'status_api.py'):
        atomic(BASE / name, base64.b64decode(config['files'][name]))
    atomic(UNIT, base64.b64decode(config['files']['machines-collector.service']))
    ssh_config = '''Host machines-gateway
    HostName 127.0.0.1
    Port 22638
    User wkli
    HostKeyAlias 10.20.6.38

Host __HOSTS__
    User wkli
    Port 22
    ProxyJump machines-gateway

Host *
    IdentityFile /var/lib/machines-collector/id_ed25519
    IdentitiesOnly yes
    BatchMode yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    StrictHostKeyChecking yes
    UserKnownHostsFile /etc/machines-collector/known_hosts
    ConnectTimeout 8
    ConnectionAttempts 1
    ServerAliveInterval 10
    ServerAliveCountMax 2
    ControlMaster auto
    ControlPersist 90
    ControlPath /run/machines-collector/ssh-%C
    LogLevel ERROR
'''.replace('__HOSTS__', ' '.join(HOSTS))
    atomic(ETC / 'ssh_config', ssh_config.encode())
    atomic(ETC / 'known_hosts', config['known_hosts'].encode())
    runtime_config = {'endpoint': config['endpoint'], 'tokens': config['tokens'],
                      'ssh_config': str(ETC / 'ssh_config'), 'interval': 30,
                      'serve_status': {'port': 8273}}
    atomic(ETC / 'config.json', json.dumps(runtime_config).encode(), 0o600)
    subprocess.run(['systemd-analyze', 'verify', str(UNIT)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'machines-collector.service'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['systemctl', 'restart', 'machines-collector.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'machines-collector.service'], check=True)
    print(json.dumps({'installed': True, 'service': 'machines-collector.service', 'interval': 30}), flush=True)


if __name__ == '__main__':
    assert os.geteuid() == 0
    print('__MONITOR_DEPLOY_READY__', flush=True)
    private = json.loads(base64.b64decode(sys.stdin.readline().strip()))
    if private['stage'] == 'initialize':
        initialize()
    elif private['stage'] == 'install':
        install(private)
    else:
        raise ValueError('Unknown stage')
