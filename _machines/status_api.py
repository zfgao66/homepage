"""Serve authenticated monitoring and shared registrations on a loopback socket."""
import datetime
import http.server
import json
import http.cookies
import hmac
import re

from management_auth import SessionStore, AuthError
from reservations import ReservationStore, ReservationError
import pathlib
import threading
import time
import urllib.parse

DASHBOARD_PATH = pathlib.Path(__file__).with_name('dashboard.html')
DASHBOARD_MARKER = '__MACHINES_LATEST_SNAPSHOT__'

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



def safe_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def create_server(store, port, management=None):
    if not management:
        raise ValueError('Management private configuration is required; refusing public access')
    database = pathlib.Path(management['database'])
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    registrations = ReservationStore(database)
    sessions = SessionStore(database, management['password_hash'])
    origin = management.get('origin', 'https://43.155.209.84')
    dashboard = DASHBOARD_PATH.read_text() if DASHBOARD_PATH.exists() else None

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = 'MachinesManagement'
        sys_version = ''

        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def headers_common(self, status, length, content_type='application/json; charset=utf-8', extra=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def reply(self, status, body, extra=None):
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.headers_common(status, len(payload), extra=extra)
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def token(self):
            try:
                cookie = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
                return cookie[SessionStore.COOKIE].value if SessionStore.COOKIE in cookie else ''
            except http.cookies.CookieError:
                return ''

        def authenticate(self, mutation=False):
            session = sessions.session(self.token())
            if mutation:
                self.check_origin()
                csrf = self.headers.get('X-CSRF-Token', '')
                if not hmac.compare_digest(csrf, session['csrf_token']):
                    raise AuthError('页面验证已过期，请刷新后重试。', 403, 'csrf_failed')
            return session

        def check_origin(self):
            if self.headers.get('Origin') != origin:
                raise AuthError('请从资源管理页面提交操作。', 403, 'origin_not_allowed')

        def body(self):
            if self.headers.get_content_type() != 'application/json':
                raise AuthError('请求必须为 JSON。', 415, 'invalid_content_type')
            if self.headers.get('Transfer-Encoding'):
                raise AuthError('不支持分块请求。', 400, 'invalid_request')
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 1 <= length <= 32768:
                raise AuthError('请求内容过大或为空。', 413, 'invalid_body_size')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise AuthError('请求内容不完整。', 400, 'invalid_request')
            try:
                data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeDecodeError):
                raise AuthError('请求内容格式错误。', 400, 'invalid_json')
            if not isinstance(data, dict):
                raise AuthError('请求内容必须为对象。', 400, 'invalid_json')
            return data

        def do_OPTIONS(self):
            self.reply(403, {'error': '请直接访问 HTTPS 资源管理页面。', 'code': 'same_origin_required', 'details': {}})

        def execute(self, action):
            try:
                action()
            except (AuthError, ReservationError) as error:
                self.reply(error.status, {'error': str(error), 'code': error.code, 'details': error.details})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except Exception as error:
                # Never log passwords, cookies, names, or request bodies.
                print(json.dumps({'event': 'management_api_error', 'type': type(error).__name__}), flush=True)
                self.reply(500, {'error': '暂时无法完成操作，请稍后重试。', 'code': 'internal_error', 'details': {}})

        def read(self):
            parsed = urllib.parse.urlsplit(self.path)
            path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
            if path in ('/machines', '/machines/') and dashboard is not None:
                try:
                    session = sessions.session(self.token())
                except AuthError:
                    session = {'authenticated': False}
                payload = dashboard.replace(DASHBOARD_MARKER, safe_json(store.response()) if session['authenticated'] else 'null')
                payload = payload.replace('__MACHINES_INITIAL_SESSION__', safe_json(session)).encode()
                self.headers_common(200, len(payload), 'text/html; charset=utf-8', {
                    'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"})
                if self.command != 'HEAD':
                    self.wfile.write(payload)
                return
            session = self.authenticate()
            if path == '/api/session':
                self.reply(200, session)
            elif path == '/api/status':
                self.reply(200, store.response())
            elif path == '/api/registrations':
                records = registrations.list_records(history=query.get('history', ['0'])[0] == '1', machine_id=query.get('machine_id', [None])[0])
                self.reply(200, {'registrations': records, 'generated_at': timestamp(time.time())})
            elif path == '/api/overview':
                self.reply(200, registrations.overview())
            elif path == '/api/statistics':
                try:
                    days = int(query.get('days', ['7'])[0])
                except ValueError:
                    raise AuthError('统计周期应为 7 天或 30 天。', 400, 'validation_error')
                self.reply(200, registrations.statistics(days))
            elif re.fullmatch(r'/api/registrations/[a-f0-9-]{36}/history', path):
                self.reply(200, {'events': registrations.history(path.split('/')[3])})
            else:
                self.reply(404, {'error': '没有找到该接口。', 'code': 'not_found', 'details': {}})

        def write(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == '/api/login' and self.command == 'POST':
                self.check_origin()
                data = self.body()
                token, session = sessions.login(data.get('password'), self.headers.get('X-Real-IP', self.client_address[0]))
                self.reply(200, session, {'Set-Cookie': '%s=%s; Path=/; Secure; HttpOnly; SameSite=Lax' % (SessionStore.COOKIE, token)})
                return
            self.authenticate(mutation=True)
            data = self.body()
            if path == '/api/logout' and self.command == 'POST':
                sessions.logout(self.token())
                self.reply(200, {'authenticated': False}, {'Set-Cookie': '%s=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax' % SessionStore.COOKIE})
                return
            if path == '/api/registrations/preview' and self.command == 'POST':
                exclude_id = data.pop('exclude_id', None)
                self.reply(200, registrations.preview(data, exclude_id=exclude_id))
                return
            request_id = self.headers.get('Idempotency-Key', '')
            if path == '/api/registrations' and self.command == 'POST':
                self.reply(201, {'registration': registrations.create(data, request_id)})
                return
            match = re.fullmatch(r'/api/registrations/([a-f0-9-]{36})(?:/(extend|end|cancel))?', path)
            if match:
                identity, action = match.groups()
                expected_version = data.pop('expected_version', None)
                if action and self.command == 'POST':
                    record = registrations.action(identity, action, data, expected_version, request_id)
                elif not action and self.command == 'PATCH':
                    record = registrations.update(identity, data, expected_version, request_id)
                else:
                    self.reply(405, {'error': '操作方式不支持。', 'code': 'method_not_allowed', 'details': {}})
                    return
                self.reply(200, {'registration': record})
                return
            self.reply(404, {'error': '没有找到该接口。', 'code': 'not_found', 'details': {}})

        def do_GET(self):
            self.execute(self.read)

        do_HEAD = do_GET

        def do_POST(self):
            self.execute(self.write)

        do_PATCH = do_POST

    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.registrations, server.sessions = registrations, sessions
    return server


def start_server(store, port, management=None):
    server = create_server(store, port, management)
    threading.Thread(target=server.serve_forever, name='status-api', daemon=True).start()
    return server
