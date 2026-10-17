import json
import threading
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

import collector
import status_api
import build_dashboard


class DirectStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.store = status_api.LatestSnapshots(('10.20.6.38', '10.20.5.8'), clock=lambda: self.now)
        self.snapshot = {'machine_id': '10.20.6.38', 'observed_at': status_api.timestamp(self.now), 'gpus': []}

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

    def test_api_is_loopback_read_only_and_returns_cors_for_the_website(self):
        server = status_api.create_server(self.store, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            url = 'http://127.0.0.1:%s/api/status' % server.server_address[1]
            for origin in ('http://gaozefeng.tech', 'https://www.gaozefeng.tech', 'https://unrelated.test'):
                with urllib.request.urlopen(urllib.request.Request(url, headers={'Origin': origin})) as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin if origin in status_api.ALLOWED_ORIGINS else None)
                    self.assertEqual(json.load(response)['schema_version'], 1)
            preflight = urllib.request.Request(url, method='OPTIONS', headers={
                'Origin': 'http://gaozefeng.tech', 'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'cache-control, pragma'})
            with urllib.request.urlopen(preflight) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.read(), b'')
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), 'http://gaozefeng.tech')
                self.assertIn('GET', response.headers['Access-Control-Allow-Methods'])
                self.assertIn('Cache-Control', response.headers['Access-Control-Allow-Headers'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, method='OPTIONS', headers={
                    'Origin': 'https://unrelated.test', 'Access-Control-Request-Method': 'GET'}))
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, data=b'{}', method='POST'))
            self.assertEqual(error.exception.code, 501)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url.replace('/api/status', '/config.json'))
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_same_origin_page_contains_latest_data_without_external_subresources(self):
        bundle = build_dashboard.build()
        self.assertNotIn('{%', bundle)
        self.assertNotIn('<script src=', bundle)
        self.assertNotIn('<link rel="stylesheet"', bundle)
        with tempfile.TemporaryDirectory() as directory:
            page = pathlib.Path(directory) / 'dashboard.html'
            page.write_text(bundle)
            with mock.patch.object(status_api, 'DASHBOARD_PATH', page):
                server = status_api.create_server(self.store, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = 'http://127.0.0.1:%s/machines/?embed=1' % server.server_address[1]
                self.store.publish(dict(self.snapshot, cpu_model='</script><script>alert(1)</script>'))
                with urllib.request.urlopen(url) as response:
                    html = response.read().decode()
                    self.assertEqual(response.headers.get_content_type(), 'text/html')
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
                    self.assertIn('frame-ancestors http://gaozefeng.tech', response.headers['Content-Security-Policy'])
                    self.assertNotIn(status_api.DASHBOARD_MARKER, html)
                    self.assertNotIn('</script><script>alert(1)', html)
                    data = json.loads(html.split('<script id="machines-initial-status" type="application/json">')[1].split('</script>')[0])
                    self.assertEqual(data['machines'][0]['age_seconds'], 0)
                self.now += 600
                with urllib.request.urlopen(url) as response:
                    html = response.read().decode()
                    data = json.loads(html.split('<script id="machines-initial-status" type="application/json">')[1].split('</script>')[0])
                    self.assertEqual(data['machines'][0]['age_seconds'], 600)
                    self.assertEqual(data['machines'][0]['status'], 'stale')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

