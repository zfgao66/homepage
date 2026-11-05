#!/usr/bin/env python3
"""Deploy the private management extension, retaining the collector and snapshots.

Run as root. Read one base64 JSON payload from stdin. Public source contains no
password; payload password_hash and existing collector configuration stay private.
"""
import base64
import datetime
import json
import os
import pathlib
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path('/opt/machines-collector')
CONFIG = pathlib.Path('/etc/machines-collector/config.json')
NGINX = pathlib.Path('/etc/nginx/conf.d/machines-status-https.conf')
DATABASE = pathlib.Path('/var/lib/machines-collector/registrations.sqlite3')
FILES = {'collector.py', 'status_api.py', 'management_auth.py', 'reservations.py',
         'dashboard.html', 'install_management.py', 'setup_status_https.py', 'usage_accounting.py'}


def nginx_config():
    return '''server {
    listen 443 ssl;
    server_name 43.155.209.84;
    ssl_certificate /etc/machines-monitor-tls/live/machines-api/fullchain.pem;
    ssl_certificate_key /etc/machines-monitor-tls/live/machines-api/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    keepalive_timeout 10s;
    client_max_body_size 32k;
    client_body_timeout 10s;
    location ^~ /machines-api/ {
        proxy_pass http://127.0.0.1:8273/api/;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Host $host;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
        proxy_cache off;
        access_log off;
    }
    location = /machines { return 302 /machines/; }
    location = /machines/ {
        proxy_pass http://127.0.0.1:8273/machines/;
        proxy_connect_timeout 3s;
        proxy_read_timeout 5s;
        proxy_cache off;
        access_log off;
    }
    location / { return 404; }
}
'''


def atomic(path, content, mode=0o644):
    temporary = path.with_name('.' + path.name + '.management-next')
    temporary.write_bytes(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def install(payload):
    if os.geteuid() != 0 or set(payload['files']) != FILES:
        raise ValueError('Root and the exact release manifest are required')
    files = {name: base64.b64decode(value, validate=True) for name, value in payload['files'].items()}
    for name, contents in files.items():
        if name.endswith('.py'):
            compile(contents, name, 'exec')
    if not payload['password_hash'].startswith('pbkdf2_sha256$'):
        raise ValueError('Supply a password hash, never a plaintext password')
    backup = pathlib.Path('/var/lib/machines-management-deploy-backups') / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    paths = [ROOT / name for name in FILES] + [CONFIG, NGINX]
    old = {str(path): path.read_bytes() if path.exists() else None for path in paths}
    # One pre-deployment backup, not a continuing stream of monitoring history.
    for index, (name, data) in enumerate(old.items()):
        if data is not None:
            atomic(backup / str(index), data, 0o600)
    atomic(backup / 'manifest.json', json.dumps({name: index if data is not None else None for index, (name, data) in enumerate(old.items())}).encode(), 0o600)
    if DATABASE.exists():
        with sqlite3.connect(DATABASE) as source, sqlite3.connect(backup / 'registrations.sqlite3') as target:
            source.backup(target)
        os.chmod(backup / 'registrations.sqlite3', 0o600)
    config = json.loads(old[str(CONFIG)])
    config['serve_status']['management'] = {'database': str(DATABASE), 'password_hash': payload['password_hash'], 'origin': 'https://43.155.209.84'}
    config['endpoint'] = ''  # Stop publishing monitoring data to the old public backup.
    try:
        run('systemctl', 'stop', 'machines-collector.service')
        for name, contents in files.items():
            atomic(ROOT / name, contents)
        atomic(CONFIG, json.dumps(config, ensure_ascii=False, indent=2).encode(), 0o600)
        atomic(NGINX, nginx_config().encode())
        run('/usr/sbin/nginx', '-t')
        run('systemctl', 'start', 'machines-collector.service')
        ready = False
        for _ in range(20):
            try:
                urllib.request.urlopen('http://127.0.0.1:8273/api/status', timeout=1)
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    ready = True
                    break
            except OSError:
                pass
            time.sleep(.25)
        if not ready:
            raise RuntimeError('Protected status API did not start')
        run('systemctl', 'reload', 'nginx')
    except Exception:
        for name, data in old.items():
            path = pathlib.Path(name)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, data, 0o600 if path == CONFIG else 0o644)
        run('systemctl', 'restart', 'machines-collector.service')
        run('/usr/sbin/nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        raise
    print(json.dumps({'deployed': True, 'backup': str(backup), 'database': str(DATABASE), 'anonymous_status': 401}))


if __name__ == '__main__':
    print('__MONITOR_DEPLOY_READY__', flush=True)
    install(json.loads(base64.b64decode(input(), validate=True)))
