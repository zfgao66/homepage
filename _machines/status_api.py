"""Serve only the latest read-only snapshots on a loopback socket."""
import datetime
import http.server
import json
import pathlib
import threading
import time
import urllib.parse

DASHBOARD_PATH = pathlib.Path(__file__).with_name('dashboard.html')
DASHBOARD_MARKER = '__MACHINES_LATEST_SNAPSHOT__'

ALLOWED_ORIGINS = frozenset(('http://gaozefeng.tech', 'https://gaozefeng.tech',
                             'http://www.gaozefeng.tech', 'https://www.gaozefeng.tech',
                             'http://127.0.0.1:8788'))


def timestamp(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()


class LatestSnapshots:
    def __init__(self, hosts, clock=time.time):
        self.hosts = tuple(hosts)
        self.clock = clock
        self.records = {}
        self.lock = threading.Lock()

    def publish(self, snapshot):
        machine_id = snapshot['machine_id']
        if machine_id not in self.hosts:
            raise ValueError('Unexpected machine')
        with self.lock:
            self.records[machine_id] = (self.clock(), snapshot)

    def response(self):
        now = self.clock()
        with self.lock:
            records = dict(self.records)
        machines = []
        for machine_id in self.hosts:
            record = records.get(machine_id)
            age = max(0, now - record[0]) if record else None
            machines.append({'id': machine_id,
                             'status': 'waiting' if record is None else 'online' if age <= 120 else 'stale',
                             'age_seconds': age,
                             'received_at': timestamp(record[0]) if record else None,
                             'telemetry': record[1] if record else None})
        return {'schema_version': 1, 'generated_at': timestamp(now), 'machines': machines}


def create_server(store, port):
    # Read the small immutable bundle once; never retain rendered snapshots or history.
    dashboard = DASHBOARD_PATH.read_text() if DASHBOARD_PATH.exists() else None
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = 'MachinesStatus'
        sys_version = ''

        def log_message(self, *_):
            pass

        def reply(self, status, body):
            payload = b'' if status == 204 else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
            self.send_header('Vary', 'Origin')
            origin = self.headers.get('Origin')
            if origin in ALLOWED_ORIGINS:
                self.send_header('Access-Control-Allow-Origin', origin)
                if self.command == 'OPTIONS' and status == 204:
                    self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                    self.send_header('Access-Control-Allow-Headers', 'Accept, Cache-Control, Content-Type, Pragma')
                    self.send_header('Access-Control-Max-Age', '600')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def do_OPTIONS(self):
            if urllib.parse.urlsplit(self.path).path != '/api/status':
                self.reply(404, {'error': 'Not found'})
                return
            requested = {item.strip().lower() for item in self.headers.get('Access-Control-Request-Headers', '').split(',') if item.strip()}
            if (self.headers.get('Origin') not in ALLOWED_ORIGINS
                    or self.headers.get('Access-Control-Request-Method', 'GET').upper() not in ('GET', 'HEAD')
                    or not requested.issubset({'accept', 'cache-control', 'content-type', 'pragma'})):
                self.reply(403, {'error': 'Preflight not allowed'})
                return
            self.reply(204, None)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ('/machines', '/machines/') and dashboard is not None:
                # Inline the current data in the first HTML response, with script-safe JSON.
                snapshot = json.dumps(store.response(), ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                payload = dashboard.replace(DASHBOARD_MARKER, snapshot).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors http://gaozefeng.tech https://gaozefeng.tech http://www.gaozefeng.tech https://www.gaozefeng.tech http://127.0.0.1:8788")
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(payload)
                return
            if path != '/api/status':
                self.reply(404, {'error': 'Not found'})
                return
            self.reply(200, store.response())

        do_HEAD = do_GET

    # Only nginx can reach this socket; no credentials or write endpoints exist.
    return http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)


def start_server(store, port):
    server = create_server(store, port)
    threading.Thread(target=server.serve_forever, name='status-api', daemon=True).start()
    return server
