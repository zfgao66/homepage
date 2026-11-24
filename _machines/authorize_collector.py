#!/usr/bin/env python3
"""Append one restricted public key while preserving all existing SSH access."""
import base64
import fcntl
import json
import os
import pathlib
import re
import sys

HOSTS = dict.fromkeys(('10.20.6.38', '10.20.5.8', '10.20.5.73', '10.20.5.100',
                      '10.20.5.51', '10.20.6.135', '10.20.1.21'), 22)
HOSTS['10.20.1.25'] = 10001

print('__MONITOR_DEPLOY_READY__', flush=True)
config = json.loads(base64.b64decode(sys.stdin.readline().strip()))
key = config['public_key'].split()
assert len(key) == 3 and key[0] == 'ssh-ed25519' and key[2] == 'ruc-nova-gpu-collector'
assert re.fullmatch(r'[A-Za-z0-9+/=]+', key[1])
if config['role'] in ('machine', 'gateway-machine'):
    ip = config['machine_id']
    assert ip in HOSTS
    options = 'restrict,command="/usr/bin/python3 /opt/machines-monitor/agent.py --once --machine-id ' + ip + '"'
    previous_lines = [options + ' ' + ' '.join(key)]
    if config['role'] == 'gateway-machine':
        assert ip == '10.20.6.38'
        gateway = options.replace('restrict,', 'restrict,port-forwarding,', 1)
        destinations = [f'permitopen="{host}:{port}"' for host, port in HOSTS.items()]
        previous_lines.append(','.join([gateway, *destinations[:-1]]) + ' ' + ' '.join(key))
        options = ','.join([gateway, *destinations])
elif config['role'] == 'remove':
    options = ''
else:
    raise ValueError('Unknown key role')
line = options + ' ' + ' '.join(key)
directory = pathlib.Path.home() / '.ssh'
directory.mkdir(mode=0o700, exist_ok=True)
assert not directory.is_symlink() and directory.stat().st_uid == os.getuid()
os.chmod(directory, 0o700)
path = directory / 'authorized_keys'
assert not path.is_symlink()
fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
with os.fdopen(fd, 'r+') as stream:
    fcntl.flock(stream, fcntl.LOCK_EX)
    existing = stream.read()
    matching = [row for row in existing.splitlines() if key[1] in row.split()]
    if config['role'] == 'remove':
        assert all('ruc-nova-gpu-collector' in row for row in matching)
        stream.seek(0)
        stream.write(''.join(row + '\n' for row in existing.splitlines() if key[1] not in row.split()))
        stream.truncate()
    elif matching == [line]:
        pass
    elif config['role'] == 'gateway-machine' and len(matching) == 1 and matching[0] in previous_lines:
        stream.seek(0)
        stream.write(existing.replace(matching[0], line))
        stream.truncate()
    elif matching:
        raise RuntimeError('This key already has different restrictions')
    else:
        stream.seek(0, os.SEEK_END)
        stream.write(('\n' if existing and not existing.endswith('\n') else '') + line + '\n')
    stream.flush()
    os.fsync(stream.fileno())
    os.fchmod(stream.fileno(), 0o600)
print(json.dumps({'authorized': True, 'role': config['role'], 'machine_id': config.get('machine_id')}), flush=True)
