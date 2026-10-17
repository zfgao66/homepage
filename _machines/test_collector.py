import datetime
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 10, 17, 6, 0, tzinfo=datetime.timezone.utc)
        self.snapshot = {'machine_id': '10.20.6.38', 'observed_at': self.now.isoformat(), 'gpus': []}

    def test_only_fresh_snapshots_for_the_expected_machine_are_accepted(self):
        self.assertEqual(collector.parse_snapshot(json.dumps(self.snapshot), '10.20.6.38', self.now), self.snapshot)
        with self.assertRaises(ValueError):
            collector.parse_snapshot(json.dumps(self.snapshot), '10.20.5.8', self.now)
        for age in (121, -31):
            data = dict(self.snapshot, observed_at=(self.now - datetime.timedelta(seconds=age)).isoformat())
            with self.assertRaises(ValueError):
                collector.parse_snapshot(json.dumps(data), '10.20.6.38', self.now)

    def test_one_unreachable_machine_does_not_prevent_other_uploads(self):
        config = {'tokens': {'10.20.6.38': 'a', '10.20.5.8': 'b'}, 'endpoint': 'https://example.test/api/ingest', 'ssh_config': '/tmp/config'}
        def attempt(ip, *_):
            return {'ip': ip, 'ok': ip != '10.20.5.8'}
        with mock.patch.object(collector, 'collect_and_upload', side_effect=attempt):
            results = collector.run_cycle(config)
        self.assertEqual({row['ip']: row['ok'] for row in results}, {'10.20.6.38': True, '10.20.5.8': False})

    def test_ssh_failure_never_uploads_cached_or_missing_data(self):
        with mock.patch.object(collector.subprocess, 'run', side_effect=collector.subprocess.TimeoutExpired('ssh', 35)), mock.patch.object(collector.agent, 'upload') as upload:
            result = collector.collect_and_upload('10.20.6.38', 'secret-fixture', 'https://example.test/api/ingest', '/tmp/config')
        self.assertFalse(result['ok'])
        upload.assert_not_called()
        self.assertNotIn('secret-fixture', json.dumps(result))

    def test_health_logging_suppresses_samples_but_reports_failure_and_recovery(self):
        reporter = collector.StatusReporter()
        healthy = {'ip': '10.20.6.38', 'ok': True, 'gpus': 4, 'observed_at': 'first'}
        failed = {'ip': healthy['ip'], 'ok': False, 'error': 'TimeoutExpired'}
        output = io.StringIO()
        with redirect_stdout(output):
            reporter.report([healthy], now=0)
            for second in range(30, 3600, 30):
                reporter.report([dict(healthy, observed_at=str(second))], now=second)
            reporter.report([failed], now=3600)
            reporter.report([failed], now=3630)
            reporter.report([healthy], now=3660)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row['ok'] for row in rows if 'ok' in row], [True, False, True])
        heartbeat = [row for row in rows if row.get('event') == 'collector_health']
        self.assertEqual(len(heartbeat), 1)
        self.assertEqual(heartbeat[0]['unavailable'], [healthy['ip']])
        self.assertEqual(len(reporter.states), 1)

    def test_collection_does_not_write_successful_snapshots_to_logs(self):
        config = {'tokens': {'10.20.6.38': 'secret-fixture'}, 'endpoint': 'https://example.test/api/ingest', 'ssh_config': '/tmp/config'}
        output = io.StringIO()
        with mock.patch.object(collector, 'collect_and_upload', return_value={'ip': '10.20.6.38', 'ok': True}), redirect_stdout(output):
            self.assertEqual(len(collector.run_cycle(config)), 1)
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
