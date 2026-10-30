import hashlib
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from management_auth import AuthError, SessionStore, password_hash


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = password_hash('fixture-secret', 100000)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / 'private.sqlite3'
        self.now = 1800000000
        self.store = SessionStore(self.path, self.encoded, clock=lambda: self.now)

    def error(self, call, status, code):
        with self.assertRaises(AuthError) as raised:
            call()
        self.assertEqual((raised.exception.status, raised.exception.code), (status, code))

    def test_password_hash_is_salted_and_private_config_is_validated(self):
        other = password_hash('fixture-secret', 100000)
        self.assertNotEqual(other, self.encoded)
        self.assertNotIn('fixture-secret', self.encoded)
        for value in ['bad', 'sha1$100000$aa$bb', self.encoded.replace('$100000$', '$2$')]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SessionStore(self.path, value)

    def test_database_stores_token_fingerprint_and_sessions_survive_reopen(self):
        token, session = self.store.login('fixture-secret', 'client-a')
        self.assertTrue(session['authenticated'])
        self.assertNotIn(token, session.values())
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT token_hash, csrf, expires FROM auth_sessions').fetchone()
            self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(row[0], token)
            self.assertEqual(row[1], session['csrf_token'])
            self.assertEqual(row[2], self.now + SessionStore.LIFETIME)
        reopened = SessionStore(self.path, self.encoded, clock=lambda: self.now)
        self.assertEqual(reopened.session(token), session)
        self.error(lambda: reopened.session('invalid'), 401, 'authentication_required')
        self.error(lambda: reopened.session('x' * 257), 401, 'authentication_required')

    def test_expiry_is_enforced_at_exact_boundary_and_logout_revokes(self):
        token, session = self.store.login('fixture-secret', 'client-a')
        self.now += SessionStore.LIFETIME - 1
        self.assertEqual(self.store.session(token), session)
        self.now += 1
        self.error(lambda: self.store.session(token), 401, 'authentication_required')
        new, _ = self.store.login('fixture-secret', 'client-a')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0], 1)
        self.store.logout(new)
        self.error(lambda: self.store.session(new), 401, 'authentication_required')

    def test_password_shape_and_rate_limit_prevent_unbounded_hash_work(self):
        for index, password in enumerate([None, 123, '', 'x' * 129]):
            self.error(lambda: self.store.login(password, 'shape-' + str(index)), 401, 'invalid_password')
        with mock.patch('management_auth.hashlib.pbkdf2_hmac', wraps=hashlib.pbkdf2_hmac) as hashing:
            for _ in range(10):
                self.error(lambda: self.store.login('wrong', 'client-limited'), 401, 'invalid_password')
            self.assertEqual(hashing.call_count, 10)
            self.error(lambda: self.store.login('fixture-secret', 'client-limited'), 429, 'rate_limited')
            self.assertEqual(hashing.call_count, 10)
        self.now += 300
        self.assertTrue(self.store.login('fixture-secret', 'client-limited')[1]['authenticated'])

    def test_hash_concurrency_limit_fails_boundedly_and_recovers(self):
        self.assertTrue(self.store.verifiers.acquire(False))
        self.assertTrue(self.store.verifiers.acquire(False))
        try:
            self.error(lambda: self.store.login('fixture-secret', 'client-a'), 429, 'rate_limited')
        finally:
            self.store.verifiers.release()
            self.store.verifiers.release()
        self.assertTrue(self.store.login('fixture-secret', 'client-a')[1]['authenticated'])


if __name__ == '__main__':
    unittest.main()
