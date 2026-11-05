"""Real HTTP integration tests; all fixture state stays in temporary SQLite files."""
import datetime
import http.client
import http.cookies
import json
import pathlib
import tempfile
import threading
import unittest
import uuid
from unittest import mock

import collector
import management_auth
import status_api

ORIGIN = 'https://43.155.209.84'
DAY = 86400
UNSET = object()


class Browser:
    def __init__(self, server):
        self.server, self.cookie, self.csrf = server, '', ''

    def request(self, method, path, data=UNSET, *, origin=ORIGIN, csrf=UNSET,
                request_id=None, raw=None, headers=None):
        outgoing = {'Cookie': self.cookie} if self.cookie else {}
        if method in ('POST', 'PATCH'):
            if origin is not None:
                outgoing['Origin'] = origin
            outgoing.update({'Content-Type': 'application/json',
                             'X-CSRF-Token': self.csrf if csrf is UNSET else csrf})
        if data is not UNSET:
            raw = json.dumps(data, ensure_ascii=False).encode()
        if request_id is not None:
            outgoing['Idempotency-Key'] = request_id
        outgoing.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body=raw, headers=outgoing)
            response = connection.getresponse()
            body = response.read()
            result_headers = dict(response.getheaders())
            result = json.loads(body) if body and response.getheader('Content-Type', '').startswith('application/json') else body
            if response.getheader('Set-Cookie'):
                cookies = http.cookies.SimpleCookie(response.getheader('Set-Cookie'))
                self.cookie = '%s=%s' % (management_auth.SessionStore.COOKIE, cookies[management_auth.SessionStore.COOKIE].value)
            if isinstance(result, dict) and result.get('csrf_token'):
                self.csrf = result['csrf_token']
            return response.status, result_headers, result
        finally:
            connection.close()

    def login(self, password='fixture-secret'):
        return self.request('POST', '/api/login', {'password': password})


class DirectStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = management_auth.password_hash('fixture-secret', 100000)

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 31, tzinfo=datetime.timezone.utc).timestamp()
        self.started = self.now
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        self.database = self.directory / 'private' / 'registrations.sqlite3'
        self.page = self.directory / 'dashboard.html'
        self.page.write_text('<!doctype html><title>资源管理</title><form>密码</form>'
            '<script id="machines-initial-status" type="application/json">__MACHINES_LATEST_SNAPSHOT__</script>'
            '<script id="machines-initial-session" type="application/json">__MACHINES_INITIAL_SESSION__</script>')
        self.store = status_api.LatestSnapshots(('10.20.6.38', '10.20.5.8'), clock=lambda: self.now)
        self.snapshot = {'machine_id': '10.20.6.38', 'observed_at': status_api.timestamp(self.now), 'gpus': []}
        self.management = {'database': str(self.database), 'password_hash': self.password_hash, 'origin': ORIGIN}
        self.server = self.thread = None
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        with mock.patch.object(status_api, 'DASHBOARD_PATH', self.page):
            self.server = status_api.create_server(self.store, 0, management=self.management)
        self.server.registrations.clock = lambda: self.now
        with self.server.registrations.usage.connect() as db:
            db.executemany('INSERT OR REPLACE INTO usage_accounts VALUES (?,?,?,?)', [(host, 'wkli', 1001, self.now) for host in collector.HOSTS])
            db.executemany('INSERT OR REPLACE INTO usage_last VALUES (?,?,?,?,?)', [(host, self.now, 'fixture', '{}', 1) for host in collector.HOSTS])
        self.server.sessions.clock = lambda: self.now
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None

    def browser(self):
        browser = Browser(self.server)
        self.assertEqual(browser.login()[0], 200)
        return browser

    def payload(self, **changes):
        data = {'machine_id': '10.20.6.38', 'name': '张同学', 'wechat': 'zhang_fixture',
                'server_username': 'wkli', 'gpu_count': 2, 'gpu_indices': [0, 1], 'start_at': status_api.timestamp(self.started),
                'end_at': status_api.timestamp(self.started + DAY), 'purpose': '测试研究用途',
                'notes': '夜间训练，使用前请先微信联系协调。', 'usage_pattern': '每天夜间使用'}
        data.update(changes)
        return data

    def create(self, browser, **changes):
        with self.server.registrations.usage.connect() as db:
            db.execute('UPDATE usage_accounts SET observed=?', (self.now,))
        payload = self.payload(**changes)
        # Seed historical long records to verify compatibility with the new limit.
        if changes.get('end_at') and (datetime.datetime.fromisoformat(payload['end_at']) - datetime.datetime.fromisoformat(payload['start_at'])).total_seconds() > 7 * DAY:
            with mock.patch('reservations._check_duration'):
                status, _, data = browser.request('POST', '/api/registrations', payload, request_id=str(uuid.uuid4()))
        else:
            status, _, data = browser.request('POST', '/api/registrations', payload, request_id=str(uuid.uuid4()))
        self.assertEqual(status, 201, data)
        return data['registration']

    @staticmethod
    def embedded(html, identity):
        return json.loads(html.split('<script id="%s" type="application/json">' % identity)[1].split('</script>')[0])

    def test_http_rejects_more_than_seven_days(self):
        browser = self.browser()
        payload = self.payload(end_at=status_api.timestamp(self.now + 7 * DAY + 1))
        for path in ('/api/registrations', '/api/registrations/preview'):
            result = browser.request('POST', path, payload, request_id=str(uuid.uuid4()))
            self.assertEqual(result[0], 400)
        payload['end_at'] = status_api.timestamp(self.now + 7 * DAY)
        self.assertEqual(browser.request('POST', '/api/registrations', payload, request_id=str(uuid.uuid4()))[0], 201)

    def test_account_is_required_and_verified_and_usage_is_returned(self):
        browser = self.browser()
        for username in ('', 'not-a-real-user'):
            status, _, _ = browser.request('POST', '/api/registrations', self.payload(server_username=username), request_id=str(uuid.uuid4()))
            self.assertEqual(status, 400)
        created = self.create(browser)
        listed = browser.request('GET', '/api/registrations')[2]['registrations']
        self.assertEqual(listed[0]['server_username'], 'wkli')
        self.assertEqual(listed[0]['usage']['account_status'], 'bound')
        self.assertIsNone(listed[0]['usage']['occupied_gpu_hours'])
        self.assertIsNone(browser.request('GET', '/api/statistics?days=7')[2]['people'][0]['usage']['occupied_gpu_hours'])

    def test_latest_only_and_stale_age_grows_without_new_collection(self):
        self.store.publish(self.snapshot)
        for value in range(100):
            self.store.publish(dict(self.snapshot, cpu_percent=value))
        self.assertEqual(len(self.store.records), 1)
        self.assertEqual(self.store.response()['machines'][0]['telemetry']['cpu_percent'], 99)
        self.now += 121
        rows = self.store.response()['machines']
        self.assertEqual((rows[0]['status'], rows[0]['age_seconds']), ('stale', 121))
        self.assertEqual(rows[1]['status'], 'waiting')
        with self.assertRaises(ValueError):
            self.store.publish(dict(self.snapshot, machine_id='unapproved'))

    def test_secondary_upload_failure_does_not_break_fresh_local_status(self):
        completed = mock.Mock(stdout=json.dumps(self.snapshot))
        with mock.patch.object(collector.subprocess, 'run', return_value=completed), mock.patch.object(collector, 'parse_snapshot', return_value=self.snapshot), mock.patch.object(collector.agent, 'upload', side_effect=TimeoutError):
            result = collector.collect_and_upload('10.20.6.38', 'secret-fixture', 'https://example.test', '/tmp/ssh', self.store)
        self.assertTrue(result['ok'])
        self.assertEqual(result['upload_error'], 'TimeoutError')
        self.assertEqual(self.store.response()['machines'][0]['status'], 'online')
        self.assertNotIn('secret-fixture', json.dumps(self.store.response()))

    def test_private_configuration_required_and_every_read_is_protected(self):
        with self.assertRaises(ValueError):
            status_api.create_server(self.store, 0)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        anonymous = Browser(self.server)
        paths = ['/api/session', '/api/status', '/api/registrations', '/api/registrations?history=1',
                 '/api/overview', '/api/statistics?days=30', '/api/registrations/%s/history' % uuid.uuid4()]
        for path in paths:
            with self.subTest(path=path):
                code, headers, body = anonymous.request('GET', path)
                self.assertEqual(code, 401)
                self.assertEqual(body['code'], 'authentication_required')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(anonymous.request('HEAD', '/api/status')[0], 401)
        self.assertEqual(anonymous.request('POST', '/api/registrations', self.payload(), request_id=str(uuid.uuid4()))[0], 401)
        anonymous.cookie = management_auth.SessionStore.COOKIE + '=invalid'
        self.assertEqual(anonymous.request('GET', '/api/status')[0], 401)

    def test_password_cookie_reload_and_anonymous_html_do_not_expose_live_data(self):
        self.store.publish(dict(self.snapshot, cpu_model='PRIVATE-SNAPSHOT-MARKER'))
        browser = Browser(self.server)
        code, headers, body = browser.request('GET', '/machines/')
        self.assertEqual(code, 200)
        html = body.decode()
        for private in ('PRIVATE-SNAPSHOT-MARKER', 'fixture-secret', self.password_hash):
            self.assertNotIn(private, html)
        self.assertIsNone(self.embedded(html, 'machines-initial-status'))
        self.assertEqual(self.embedded(html, 'machines-initial-session'), {'authenticated': False})
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(browser.login('wrong')[0], 401)
        code, headers, data = browser.login()
        self.assertEqual(code, 200)
        self.assertTrue(data['authenticated'])
        for attribute in ('__Host-machines_session=', 'Path=/', 'Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(attribute, headers['Set-Cookie'])
        self.assertNotIn('Max-Age', headers['Set-Cookie'])
        self.assertEqual(browser.request('GET', '/api/session')[2]['csrf_token'], browser.csrf)
        self.assertIn('PRIVATE-SNAPSHOT-MARKER', browser.request('GET', '/machines/')[2].decode())

    def test_script_safe_embedded_snapshot_and_real_aging(self):
        browser = self.browser()
        malicious = '</script><script>alert(1)</script>&'
        self.store.publish(dict(self.snapshot, cpu_model=malicious))
        code, headers, body = browser.request('GET', '/machines/')
        html = body.decode()
        self.assertEqual(code, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(malicious, html)
        self.assertIn('\\u003c/script\\u003e', html)
        self.assertEqual(self.embedded(html, 'machines-initial-status')['machines'][0]['telemetry']['cpu_model'], malicious)
        self.now += 600
        data = self.embedded(browser.request('GET', '/machines/')[2].decode(), 'machines-initial-status')
        self.assertEqual((data['machines'][0]['status'], data['machines'][0]['age_seconds']), ('stale', 600))

    def test_two_independent_sessions_see_shared_day_and_month_records(self):
        first, second = self.browser(), self.browser()
        self.assertNotEqual(first.cookie, second.cookie)
        day = self.create(first)
        month = self.create(first, machine_id='10.20.1.21', gpu_indices=[6, 7], end_at=status_api.timestamp(self.started + 30 * DAY))
        records = second.request('GET', '/api/registrations')[2]['registrations']
        self.assertEqual({row['id'] for row in records}, {day['id'], month['id']})
        self.assertTrue(next(row for row in records if row['id'] == month['id'])['long_term'])
        self.now += 4 * DAY
        second = self.browser()  # Session lifetime and reservation lifetime are separate.
        self.store.publish(dict(self.snapshot, gpus=[{'index': 0, 'utilization_pct': 0}]))
        records = second.request('GET', '/api/registrations')[2]['registrations']
        self.assertEqual([row['id'] for row in records], [month['id']])
        self.assertEqual(records[0]['status'], 'active')
        self.assertEqual(records[0]['planned_gpu_hours'], 1440)
        self.now = self.started + 30 * DAY
        second = self.browser()
        self.assertEqual(second.request('GET', '/api/registrations')[2]['registrations'], [])
        history = second.request('GET', '/api/registrations?history=1')[2]['registrations']
        self.assertEqual(len(history), 2)
        self.assertTrue(all(row['status'] == 'expired' for row in history))

    def test_conflict_confirmation_preserves_both_records_and_contacts(self):
        browser = self.browser()
        first = self.create(browser, end_at=status_api.timestamp(self.started + 30 * DAY))
        payload = self.payload(name='李同学', wechat='li_fixture', gpu_count=1, gpu_indices=[1],
                               start_at=status_api.timestamp(self.started + 10 * DAY), end_at=status_api.timestamp(self.started + 11 * DAY))
        preview = browser.request('POST', '/api/registrations/preview', payload)
        self.assertEqual(preview[0], 200)
        self.assertEqual(preview[2]['conflicts'][0]['wechat'], first['wechat'])
        key = str(uuid.uuid4())
        rejected = browser.request('POST', '/api/registrations', payload, request_id=key)
        self.assertEqual((rejected[0], rejected[2]['code']), (409, 'conflict_confirmation_required'))
        self.assertEqual(rejected[2]['details']['conflicts'][0]['id'], first['id'])
        payload['conflict_ack'] = True
        accepted = browser.request('POST', '/api/registrations', payload, request_id=key)
        self.assertEqual(accepted[0], 201)
        self.assertTrue(accepted[2]['registration']['has_conflict'])
        self.assertEqual(len(browser.request('GET', '/api/registrations')[2]['registrations']), 2)

    def test_mutations_require_origin_and_csrf_including_preview_and_logout(self):
        browser = self.browser()
        record = self.create(browser)
        path = '/api/registrations/' + record['id']
        mutations = [('POST', '/api/registrations/preview', self.payload()),
                     ('POST', '/api/registrations', self.payload()),
                     ('PATCH', path, {'expected_version': 1, 'notes': 'blocked'}),
                     ('POST', path + '/extend', {'expected_version': 1, 'end_at': status_api.timestamp(self.started + 30 * DAY)}),
                     ('POST', path + '/end', {'expected_version': 1}),
                     ('POST', path + '/cancel', {'expected_version': 1}),
                     ('POST', '/api/logout', {})]
        for method, path, data in mutations:
            for extra in ({'origin': 'https://evil.test'}, {'origin': None}, {'csrf': ''}, {'csrf': 'wrong'}):
                with self.subTest(path=path, extra=extra):
                    self.assertEqual(browser.request(method, path, data, request_id=str(uuid.uuid4()), **extra)[0], 403)
        another = self.browser()
        self.assertEqual(another.request('POST', '/api/registrations/preview', self.payload(), csrf=browser.csrf)[0], 403)
        self.assertEqual(browser.request('GET', '/api/session')[0], 200)
        self.assertEqual(browser.request('GET', '/api/registrations')[2]['registrations'][0]['version'], 1)
        self.assertEqual(Browser(self.server).request('POST', '/api/login', {'password': 'fixture-secret'}, origin='http://gaozefeng.tech')[0], 403)
        self.assertEqual(browser.request('OPTIONS', '/api/status', headers={'Origin': 'http://gaozefeng.tech'})[0], 403)

    def test_concurrent_http_clients_recheck_conflicts_before_commit(self):
        clients = [self.browser(), self.browser()]
        payloads = [self.payload(name='同时申请' + str(index), wechat='concurrent_' + str(index)) for index in range(2)]
        for client, payload in zip(clients, payloads):
            self.assertFalse(client.request('POST', '/api/registrations/preview', payload)[2]['requires_confirmation'])
        barrier, results = threading.Barrier(2), [None, None]

        def submit(index):
            barrier.wait(timeout=3)
            results[index] = clients[index].request('POST', '/api/registrations', payloads[index], request_id=str(uuid.uuid4()))

        workers = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(result[0] for result in results), [201, 409])
        rejected_index = next(index for index, result in enumerate(results) if result[0] == 409)
        rejected = results[rejected_index]
        self.assertEqual(rejected[2]['code'], 'conflict_confirmation_required')
        self.assertEqual(len(clients[0].request('GET', '/api/registrations')[2]['registrations']), 1)
        accepted = clients[rejected_index].request('POST', '/api/registrations', dict(payloads[rejected_index], conflict_ack=True), request_id=str(uuid.uuid4()))
        self.assertEqual(accepted[0], 201)
        self.assertEqual(len(clients[0].request('GET', '/api/registrations')[2]['registrations']), 2)

    def test_invalid_and_oversized_request_bodies_fail_without_writes(self):
        browser = self.browser()
        for raw, expected in [(b'{', 400), (b'[]', 400), (b'{"n":NaN}', 400), (b'\xff', 400), (b'x' * 32769, 413), (b'', 413)]:
            with self.subTest(size=len(raw), expected=expected):
                response = browser.request('POST', '/api/registrations', raw=raw, request_id=str(uuid.uuid4()))
                self.assertEqual(response[0], expected)
                self.assertNotIn('Traceback', json.dumps(response[2]))
        self.assertEqual(browser.request('POST', '/api/registrations', self.payload(), headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(browser.request('POST', '/api/registrations', self.payload(), headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(browser.request('GET', '/api/registrations')[2]['registrations'], [])

    def test_idempotency_versions_edit_extend_end_cancel_and_history_envelopes(self):
        browser = self.browser()
        payload, key = self.payload(), str(uuid.uuid4())
        first = browser.request('POST', '/api/registrations', payload, request_id=key)
        repeated = browser.request('POST', '/api/registrations', payload, request_id=key)
        self.assertEqual(first[2], repeated[2])
        record = first[2]['registration']
        path = '/api/registrations/' + record['id']
        self.assertEqual(browser.request('POST', '/api/registrations', dict(payload, notes='different'), request_id=key)[2]['code'], 'idempotency_conflict')
        preview = browser.request('POST', '/api/registrations/preview', {'exclude_id': record['id'], 'notes': '新备注'})
        self.assertEqual(preview[0], 200)
        self.assertFalse(preview[2]['requires_confirmation'])
        edited = browser.request('PATCH', path, {'expected_version': 1, 'notes': '新备注'}, request_id=str(uuid.uuid4()))
        self.assertEqual(edited[0], 200, edited[2])
        self.assertEqual(edited[2]['registration']['version'], 2)
        stale = browser.request('PATCH', path, {'expected_version': 1, 'notes': '旧界面'}, request_id=str(uuid.uuid4()))
        self.assertEqual((stale[0], stale[2]['code']), (409, 'version_conflict'))
        self.assertEqual(stale[2]['details']['current_version'], 2)
        extended = browser.request('POST', path + '/extend', {'expected_version': 2, 'end_at': status_api.timestamp(self.started + 7 * DAY)}, request_id=str(uuid.uuid4()))
        self.assertEqual(extended[0], 200)
        self.assertFalse(extended[2]['registration']['long_term'])
        self.now += 3600
        ended = browser.request('POST', path + '/end', {'expected_version': 3}, request_id=str(uuid.uuid4()))
        self.assertEqual((ended[0], ended[2]['registration']['status']), (200, 'ended'))
        history = browser.request('GET', path + '/history')[2]['events']
        self.assertEqual([event['action'] for event in history], ['create', 'edit', 'extend', 'end'])
        self.assertEqual(history[1]['after']['notes'], '新备注')
        future = self.create(browser, start_at=status_api.timestamp(self.now + DAY), end_at=status_api.timestamp(self.now + 2 * DAY))
        cancelled = browser.request('POST', '/api/registrations/' + future['id'] + '/cancel', {'expected_version': 1}, request_id=str(uuid.uuid4()))
        self.assertEqual(cancelled[2]['registration']['status'], 'cancelled')
        self.assertEqual(browser.request('GET', '/api/overview')[2]['active_gpu_count'], 0)
        self.assertEqual(browser.request('GET', '/api/statistics?days=7')[2]['people'][0]['gpu_hours'], 2)
        self.assertEqual(browser.request('GET', '/api/statistics?days=30')[0], 200)
        self.assertEqual(browser.request('GET', '/api/statistics?days=14')[0], 400)
        self.assertEqual(browser.request('GET', '/api/statistics?days=invalid')[0], 400)

    def test_persisted_record_and_session_survive_real_http_service_restart(self):
        browser = self.browser()
        record = self.create(browser, end_at=status_api.timestamp(self.started + 30 * DAY))
        self.stop()
        self.start()
        browser.server = self.server
        self.assertEqual(browser.request('GET', '/api/session')[0], 200)
        records = browser.request('GET', '/api/registrations')[2]['registrations']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['id'], record['id'])
        self.assertEqual(records[0]['notes'], record['notes'])
        self.assertEqual(len(browser.request('GET', '/api/registrations/' + record['id'] + '/history')[2]['events']), 1)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_logout_revokes_a_copied_cookie_and_session_expiry_blocks_writes(self):
        browser = self.browser()
        copied = Browser(self.server)
        copied.cookie, copied.csrf = browser.cookie, browser.csrf
        status, headers, data = browser.request('POST', '/api/logout', {})
        self.assertEqual(status, 200)
        self.assertFalse(data['authenticated'])
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(copied.request('GET', '/api/status')[0], 401)
        self.assertEqual(self.embedded(copied.request('GET', '/machines/')[2].decode(), 'machines-initial-session'), {'authenticated': False})
        browser = self.browser()
        self.now += management_auth.SessionStore.LIFETIME
        self.assertEqual(browser.request('GET', '/api/session')[0], 401)
        self.assertEqual(browser.request('POST', '/api/registrations', self.payload(), request_id=str(uuid.uuid4()))[0], 401)


if __name__ == '__main__':
    unittest.main()
