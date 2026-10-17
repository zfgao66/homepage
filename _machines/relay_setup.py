#!/usr/bin/env python3
"""Install the scoped reverse SSH connection without changing global SSH policy."""
import base64
import json
import os
import pathlib
import pwd
import re
import subprocess
import sys
import tempfile
import tty

MARKER = 'RUC-Nova GPU encrypted relay v1\n'


def atomic(path, content, mode=0o644):
    assert not path.is_symlink()
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def account(name):
    state = pathlib.Path('/var/lib') / name
    etc = pathlib.Path('/etc') / name
    marker = etc / '.managed'
    managed = marker.exists() and marker.read_text() == MARKER
    if not managed and (state.exists() or etc.exists()):
        raise RuntimeError('An unrelated relay directory exists')
    try:
        user = pwd.getpwnam(name)
        if not managed:
            raise RuntimeError('An unrelated relay account exists')
    except KeyError:
        subprocess.run(['useradd', '--system', '--user-group', '--home-dir', str(state),
                        '--no-create-home', '--shell', '/usr/sbin/nologin', name], check=True)
        user = pwd.getpwnam(name)
    assert user.pw_uid != 0 and user.pw_shell.endswith('nologin')
    etc.mkdir(mode=0o755, exist_ok=True)
    atomic(marker, MARKER.encode())
    state.mkdir(mode=0o700, exist_ok=True)
    os.chown(state, user.pw_uid, user.pw_gid)
    os.chmod(state, 0o700)
    return user, state, etc


assert os.geteuid() == 0
if sys.stdin.isatty():
    tty.setraw(sys.stdin.fileno())
print('__MONITOR_DEPLOY_READY__', flush=True)
config = json.loads(base64.b64decode(sys.stdin.readline().strip()))
if config['stage'] == 'initialize_sender':
    user, state, etc = account('machines-relay')
    key = state / 'id_ed25519'
    if not key.exists():
        subprocess.run(['runuser', '-u', 'machines-relay', '--', 'ssh-keygen', '-q', '-t', 'ed25519',
                        '-N', '', '-C', 'ruc-nova-gpu-relay', '-f', str(key)], check=True)
    print(json.dumps({'initialized': True, 'public_key': (state / 'id_ed25519.pub').read_text().strip()}), flush=True)
elif config['stage'] == 'install_receiver':
    user, state, etc = account('machines-tunnel')
    key = config['public_key'].split()
    assert len(key) == 3 and key[0] == 'ssh-ed25519' and key[2] == 'ruc-nova-gpu-relay'
    assert re.fullmatch(r'[A-Za-z0-9+/=]+', key[1])
    directory = state / '.ssh'
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, user.pw_uid, user.pw_gid)
    # Both forwarding directions are limited to the same VPS-local relay port.
    options = 'restrict,port-forwarding,permitlisten="127.0.0.1:22638",permitopen="127.0.0.1:22638",command="/usr/bin/false"'
    path = directory / 'authorized_keys'
    line = options + ' ' + ' '.join(key) + '\n'
    if path.exists() and path.read_text() != line:
        raise RuntimeError('Unexpected key on dedicated tunnel account')
    atomic(path, line.encode(), 0o600)
    os.chown(path, user.pw_uid, user.pw_gid)
    print(json.dumps({'receiver_ready': True, 'listen': '127.0.0.1:22638'}), flush=True)
elif config['stage'] == 'install_sender':
    user, state, etc = account('machines-relay')
    unit = pathlib.Path('/etc/systemd/system/machines-relay.service')
    if unit.exists() and 'RUC-Nova encrypted connection for read-only GPU collection' not in unit.read_text():
        raise RuntimeError('Unrelated relay service exists')
    atomic(etc / 'known_hosts', config['known_hosts'].encode())
    atomic(unit, base64.b64decode(config['unit']))
    subprocess.run(['systemd-analyze', 'verify', str(unit)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'machines-relay.service'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['systemctl', 'restart', 'machines-relay.service'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'machines-relay.service'], check=True)
    print(json.dumps({'sender_running': True}), flush=True)
else:
    raise ValueError('Unknown stage')
