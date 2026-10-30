"""Small server-side shared-password sessions; no credentials in public assets."""
import collections
import contextlib
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time


class AuthError(Exception):
    def __init__(self, message, status=401, code='authentication_required'):
        super().__init__(message)
        self.status, self.code, self.details = status, code, {}


def password_hash(password, iterations=260000):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), iterations).hex()
    return 'pbkdf2_sha256$%s$%s$%s' % (iterations, salt, digest)


class SessionStore:
    COOKIE = '__Host-machines_session'
    LIFETIME = 24 * 3600

    def __init__(self, path, encoded_password, clock=time.time):
        kind, rounds, salt, digest = encoded_password.split('$')
        if kind != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 1000000:
            raise ValueError('Invalid private password hash')
        self.rounds, self.salt, self.digest = int(rounds), bytes.fromhex(salt), bytes.fromhex(digest)
        if len(self.salt) < 16 or len(self.digest) != 32:
            raise ValueError('Invalid private password hash')
        self.path, self.clock = str(path), clock
        self.attempts, self.lock = {}, threading.Lock()
        self.verifiers = threading.BoundedSemaphore(2)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL)')

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def login(self, password, address):
        now = self.clock()
        with self.lock:
            self.attempts = {key: values for key, values in self.attempts.items() if values and values[-1] > now - 300}
            if address not in self.attempts and len(self.attempts) >= 2048:
                raise AuthError('登录请求较多，请稍后重试。', 429, 'rate_limited')
            attempts = self.attempts.setdefault(address, collections.deque())
            while attempts and attempts[0] <= now - 300:
                attempts.popleft()
            if len(attempts) >= 10:
                raise AuthError('尝试次数较多，请 5 分钟后重试。', 429, 'rate_limited')
            attempts.append(now)
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise AuthError('密码不正确，请重新输入。', 401, 'invalid_password')
        if not self.verifiers.acquire(blocking=False):
            raise AuthError('正在处理登录，请稍后重试。', 429, 'rate_limited')
        try:
            actual = hashlib.pbkdf2_hmac('sha256', password.encode(), self.salt, self.rounds)
        finally:
            self.verifiers.release()
        if not hmac.compare_digest(actual, self.digest):
            raise AuthError('密码不正确，请重新输入。', 401, 'invalid_password')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE expires <= ?', (now,))
            db.execute('INSERT INTO auth_sessions VALUES (?, ?, ?)', (self.fingerprint(token), csrf, now + self.LIFETIME))
        return token, {'authenticated': True, 'csrf_token': csrf}

    @staticmethod
    def fingerprint(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token):
        if not token or len(token) > 256:
            raise AuthError('请先输入课题组密码。')
        with self.connect() as db:
            row = db.execute('SELECT csrf, expires FROM auth_sessions WHERE token_hash = ?', (self.fingerprint(token),)).fetchone()
        if row is None or row['expires'] <= self.clock():
            raise AuthError('登录状态已过期，请重新输入密码。')
        return {'authenticated': True, 'csrf_token': row['csrf']}

    def logout(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE token_hash = ?', (self.fingerprint(token),))
