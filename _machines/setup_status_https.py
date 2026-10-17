#!/usr/bin/env python3
"""Install an isolated HTTPS entry point without changing existing app routes."""
import os
import pathlib
import subprocess

IP = '43.155.209.84'
TLS = pathlib.Path('/etc/machines-monitor-tls')
ACME = pathlib.Path('/var/lib/machines-monitor-acme')
RUNTIME = pathlib.Path('/opt/machines-certbot')


def write(path, content, mode=0o644):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.next')
    temporary.write_text(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def run(*args):
    subprocess.run(args, check=True)


def main():
    TLS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(TLS, 0o700)
    ACME.mkdir(mode=0o755, parents=True, exist_ok=True)
    nginx = pathlib.Path('/etc/nginx/sites-available/default')
    original = nginx.read_text()
    include = 'include /etc/nginx/snippets/machines-monitor-acme.conf;'
    if include not in original:
        marker = 'server_name _;'
        if original.count(marker) != 1:
            raise RuntimeError('Expected one default server; refusing to alter unknown nginx structure')
        backup = TLS / 'nginx-default-before-monitor'
        if not backup.exists():
            write(backup, original, 0o600)
        write(nginx, original.replace(marker, marker + '\n    ' + include, 1))
    write('/etc/nginx/snippets/machines-monitor-acme.conf', '''location ^~ /.well-known/acme-challenge/ {
    root /var/lib/machines-monitor-acme;
    auth_basic off;
    default_type text/plain;
    try_files $uri =404;
    access_log off;
}
''')
    try:
        run('/usr/sbin/nginx', '-t')
        run('/usr/bin/systemctl', 'reload', 'nginx')
    except Exception:
        write(nginx, original)
        raise

    certbot = RUNTIME / 'bin/certbot'
    if not certbot.exists():
        run('/usr/bin/python3', '-m', 'venv', str(RUNTIME))
        run(str(RUNTIME / 'bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--no-cache-dir', 'certbot>=5.4,<6')
    hook = TLS / 'reload-nginx'
    write(hook, '#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/usr/bin/systemctl reload nginx\n', 0o700)
    common = ['--config-dir', str(TLS), '--work-dir', '/var/lib/machines-monitor-tls',
              '--logs-dir', '/var/log/machines-monitor-tls', '--max-log-backups', '1']
    run(str(certbot), 'certonly', '--non-interactive', '--agree-tos',
        '--register-unsafely-without-email', '--preferred-profile', 'shortlived',
        '--webroot', '--webroot-path', str(ACME), '--ip-address', IP,
        '--cert-name', 'machines-api', '--deploy-hook', str(hook), *common)

    config = '''server {
    listen 443 ssl;
    server_name 43.155.209.84;
    ssl_certificate /etc/machines-monitor-tls/live/machines-api/fullchain.pem;
    ssl_certificate_key /etc/machines-monitor-tls/live/machines-api/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    keepalive_timeout 10s;
    location = /machines-api/status {
        proxy_pass http://127.0.0.1:8273/api/status;
        proxy_connect_timeout 3s;
        proxy_read_timeout 5s;
        proxy_cache off;
        access_log off;
        add_header X-Content-Type-Options nosniff always;
    }
    location = /machines { return 302 /machines/; }
    location = /machines/ {
        proxy_pass http://127.0.0.1:8273/machines/;
        proxy_connect_timeout 3s;
        proxy_read_timeout 5s;
        proxy_cache off;
        access_log off;
        add_header X-Content-Type-Options nosniff always;
    }
    location / { return 404; }
}
'''
    target = pathlib.Path('/etc/nginx/conf.d/machines-status-https.conf')
    previous = target.read_text() if target.exists() else None
    write(target, config)
    try:
        run('/usr/sbin/nginx', '-t')
        run('/usr/bin/systemctl', 'reload', 'nginx')
    except Exception:
        if previous is None:
            target.unlink()
        else:
            write(target, previous)
        raise

    renew = ' '.join([str(certbot), 'renew', '--quiet', '--no-random-sleep-on-renew', *common])
    write('/etc/systemd/system/machines-status-cert-renew.service', '''[Unit]
Description=Renew the GPU status HTTPS certificate
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart=%s
Nice=19
MemoryMax=384M
CPUQuota=50%%
''' % renew)
    write('/etc/systemd/system/machines-status-cert-renew.timer', '''[Unit]
Description=Check the GPU status HTTPS certificate every six hours
[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
RandomizedDelaySec=5min
Persistent=true
[Install]
WantedBy=timers.target
''')
    run('/usr/bin/systemctl', 'daemon-reload')
    run('/usr/bin/systemctl', 'enable', '--now', 'machines-status-cert-renew.timer')
    print('HTTPS status endpoint and automatic certificate renewal are ready', flush=True)


if __name__ == '__main__':
    main()
