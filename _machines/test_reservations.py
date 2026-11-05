import concurrent.futures
import datetime as dt
import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest import mock

from reservations import DAY, HOSTS, ReservationError, ReservationStore


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / 'registrations.sqlite3'
        self.t0 = dt.datetime(2026, 10, 31, 0, tzinfo=dt.timezone.utc).timestamp()
        self.now = self.t0
        self.store = ReservationStore(self.path, clock=lambda: self.now)
        with self.store.usage.connect() as db:
            db.executemany('INSERT OR REPLACE INTO usage_accounts VALUES (?,?,?,?)', [(host, 'wkli', 1001, self.now) for host in HOSTS])

    def payload(self, **changes):
        result = {'machine_id': '10.20.5.73', 'name': '张同学', 'wechat': 'zhang_gpu',
                  'server_username': 'wkli', 'gpu_count': 2, 'gpu_indices': [], 'start_at': iso(self.t0),
                  'end_at': iso(self.t0 + DAY), 'purpose': '模型训练',
                  'notes': '间歇计算，使用前请微信协调。', 'usage_pattern': '每天夜间使用'}
        result.update(changes)
        return result

    def create(self, **changes):
        with self.store.usage.connect() as db:
            db.execute('UPDATE usage_accounts SET observed=?', (self.now,))
        payload = self.payload(**changes)
        # Long plans in these historical-statistics fixtures predate the 7-day policy.
        try:
            legacy = dt.datetime.fromisoformat(payload['end_at']) - dt.datetime.fromisoformat(payload['start_at']) > dt.timedelta(days=7)
        except (ValueError, TypeError):
            legacy = False
        if legacy:
            with mock.patch('reservations._check_duration'):
                return self.store.create(payload, str(uuid.uuid4()))
        return self.store.create(payload, str(uuid.uuid4()))

    def assertError(self, function, code='validation_error', status=400):
        with self.assertRaises(ReservationError) as raised:
            function()
        self.assertEqual((raised.exception.code, raised.exception.status), (code, status))
        return raised.exception

    def test_seven_day_policy_cannot_be_bypassed_by_preview_edit_or_extend(self):
        over = self.payload(end_at=iso(self.t0 + 7 * DAY + 1))
        self.assertError(lambda: self.store.create(over, str(uuid.uuid4())))
        self.assertError(lambda: self.store.preview(over))
        record = self.store.create(self.payload(end_at=iso(self.t0 + 7 * DAY)), str(uuid.uuid4()))
        self.assertError(lambda: self.store.update(record['id'], {'end_at':over['end_at']}, 1, str(uuid.uuid4())))
        self.assertError(lambda: self.store.action(record['id'], 'extend', {'end_at':over['end_at']}, 1, str(uuid.uuid4())))
        self.now += DAY
        renewed = self.store.action(record['id'], 'extend', {'end_at':iso(self.now + 7 * DAY)}, 1, str(uuid.uuid4()))
        self.assertEqual(renewed['end_at'], iso(self.now + 7 * DAY).replace('+00:00', '.000000Z'))
        legacy = self.create(machine_id='10.20.6.38', end_at=iso(self.t0 + 30 * DAY))
        edited = self.store.update(legacy['id'], {'notes':'保留旧计划'}, 1, str(uuid.uuid4()))
        self.assertEqual(edited['end_at'], legacy['end_at'])
        self.assertError(lambda: self.store.action(legacy['id'], 'extend', {'end_at':iso(self.t0 + 31 * DAY)}, 2, str(uuid.uuid4())))

    def test_monthly_record_persists_without_any_telemetry_and_survives_reopen(self):
        record = self.create(end_at=iso(self.t0 + 30 * DAY), gpu_indices=[0, 1])
        self.assertTrue(record['long_term'])
        self.assertEqual(record['planned_gpu_hours'], 1440)
        self.now += 20 * DAY
        reopened = ReservationStore(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.get(record['id'])['status'], 'active')
        self.assertEqual(len(reopened.list_records()), 1)
        self.assertEqual(reopened.get(record['id'])['notes'], record['notes'])
        self.now = self.t0 + 30 * DAY
        self.assertEqual(reopened.get(record['id'])['status'], 'expired')
        self.assertEqual(reopened.list_records(), [])
        self.assertEqual(len(reopened.list_records(history=True)), 1)
        self.assertEqual(len(reopened.history(record['id'])), 1)

    def test_inventory_h100_eight_and_long_term_boundary(self):
        self.assertEqual(sum(HOSTS.values()), 32)
        record = self.create(machine_id='10.20.1.21', gpu_count=8, gpu_indices=list(range(8)), end_at=iso(self.t0 + 7 * DAY))
        self.assertFalse(record['long_term'])
        self.assertError(lambda: self.create(machine_id='10.20.1.21', gpu_count=9))
        self.assertTrue(self.create(machine_id='10.20.6.38', end_at=iso(self.t0 + 7 * DAY + 1))['long_term'])

    def test_beijing_month_boundary_and_validation(self):
        record = self.create(start_at='2026-10-31T23:00:00+08:00', end_at='2026-11-30T23:00:00+08:00')
        self.assertEqual(record['start_at'], '2026-10-31T15:00:00.000000Z')
        self.assertEqual(record['continuous_days'], 30)
        invalid = [dict(gpu_count=True), dict(gpu_count=0), dict(gpu_count=5),
                   dict(gpu_indices=[0, 0]), dict(gpu_indices=[0, 4]), dict(gpu_indices=[False, 1]),
                   dict(gpu_indices=[0]), dict(gpu_indices='0,1'), dict(machine_id=[]),
                   dict(machine_id='10.20.6.11'), dict(name=''), dict(wechat=''),
                   dict(name='a' * 81), dict(notes='x' * 4001), dict(notes='bad\x00'),
                   dict(start_at='2026-11-01T00:00:00'), dict(end_at='2026-02-30T00:00:00+08:00'),
                   dict(end_at=iso(self.t0)), dict(gpu_count=float('nan')),
                   dict(gpu_count=float('inf')), dict(conflict_ack='true')]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertError(lambda: self.create(**changes))

    def test_text_remains_plain_data_instead_of_becoming_html(self):
        value = '<script>alert("hello")</script> & <b>备注</b>'
        record = self.create(notes=value)
        self.assertEqual(self.store.get(record['id'])['notes'], value)
        self.assertEqual(self.store.history(record['id'])[0]['after']['notes'], value)

    def test_half_open_intervals_and_explicit_gpu_conflicts(self):
        first = self.create(gpu_indices=[0, 1], end_at=iso(self.t0 + 3600))
        second = self.create(gpu_indices=[0, 1], start_at=iso(self.t0 + 3600), end_at=iso(self.t0 + 7200))
        self.assertFalse(second['has_conflict'])
        overlap = self.payload(gpu_count=1, gpu_indices=[1], end_at=iso(self.t0 + 1800), wechat='li_gpu')
        preview = self.store.preview(overlap)
        self.assertTrue(preview['requires_confirmation'])
        self.assertEqual(preview['conflicts'][0]['reason'], 'gpu_overlap')
        self.assertEqual(preview['conflicts'][0]['wechat'], first['wechat'])
        key = str(uuid.uuid4())
        self.assertError(lambda: self.store.create(overlap, key), 'conflict_confirmation_required', 409)
        accepted = self.store.create(dict(overlap, conflict_ack=True), key)
        self.assertTrue(accepted['has_conflict'])
        self.assertTrue(accepted['conflict_acknowledged'])
        self.assertEqual(len(self.store.list_records(history=True)), 3)
        self.assertEqual(self.store.get(first['id'])['gpu_indices'], [0, 1])

    def test_capacity_uses_peak_concurrency_not_all_overlapping_rows(self):
        self.create(start_at=iso(self.t0), end_at=iso(self.t0 + 3600))
        self.create(start_at=iso(self.t0 + 3600), end_at=iso(self.t0 + 7200))
        candidate = self.payload(start_at=iso(self.t0), end_at=iso(self.t0 + 7200))
        self.assertEqual(self.store.preview(candidate)['conflicts'], [])
        accepted = self.store.create(candidate, str(uuid.uuid4()))
        over_capacity = self.payload(gpu_count=1, gpu_indices=[3], end_at=iso(self.t0 + 7200))
        conflicts = self.store.preview(over_capacity)['conflicts']
        self.assertEqual(len(conflicts), 3)
        self.assertTrue(all(item['reason'] == 'capacity_exceeded' for item in conflicts))
        self.assertIn(accepted['id'], [item['id'] for item in conflicts])

    def test_disjoint_explicit_cards_do_not_conflict(self):
        self.create(gpu_indices=[0, 1])
        self.assertFalse(self.create(gpu_indices=[2, 3])['has_conflict'])

    def test_concurrent_create_rechecks_after_preview_in_transaction(self):
        barrier = threading.Barrier(2)
        payload = self.payload(gpu_indices=[0, 1, 2], gpu_count=3)
        def run(index):
            store = ReservationStore(self.path, clock=lambda: self.now)
            self.assertEqual(store.preview(payload)['conflicts'], [])
            barrier.wait(timeout=5)
            try:
                return store.create(dict(payload, wechat='person_' + str(index)), str(uuid.uuid4()))
            except ReservationError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, (1, 2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('conflict_confirmation_required', results)
        self.assertEqual(len(self.store.list_records(history=True)), 1)

    def test_idempotency_retry_exact_response_even_after_later_edit(self):
        key = str(uuid.uuid4())
        payload = self.payload()
        original = self.store.create(payload, key)
        edited = self.store.update(original['id'], {'notes': '新的备注'}, 1, str(uuid.uuid4()))
        self.assertEqual(self.store.create(payload, key), original)
        self.assertEqual(self.store.get(original['id'])['notes'], '新的备注')
        self.assertEqual(edited['version'], 2)
        self.assertError(lambda: self.store.create(dict(payload, notes='different'), key), 'idempotency_conflict', 409)
        self.assertEqual(len(self.store.history(original['id'])), 2)

    def test_concurrent_idempotency_creates_only_one_record(self):
        key = str(uuid.uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: self.store.create(self.payload(), key), range(6)))
        self.assertEqual(len({record['id'] for record in results}), 1)
        self.assertTrue(all(record == results[0] for record in results))
        self.assertEqual(len(self.store.history(results[0]['id'])), 1)

    def test_concurrent_edit_requires_expected_version(self):
        record = self.create()
        barrier = threading.Barrier(2)
        def run(index):
            barrier.wait(timeout=5)
            try:
                return self.store.update(record['id'], {'notes': str(index)}, 1, str(uuid.uuid4()))
            except ReservationError as error:
                return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, (1, 2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('version_conflict', results)
        self.assertEqual(len(self.store.history(record['id'])), 2)
        self.assertError(lambda: self.store.update(record['id'], {'notes': 'x'}, True, str(uuid.uuid4())))

    def test_future_edit_end_cancel_and_history(self):
        record = self.create(start_at=iso(self.t0 + DAY), end_at=iso(self.t0 + 2 * DAY))
        updated = self.store.update(record['id'], {'notes': '夜间训练'}, 1, str(uuid.uuid4()))
        self.assertEqual(updated['status'], 'scheduled')
        self.assertError(lambda: self.store.action(record['id'], 'end', {}, 2, str(uuid.uuid4())), 'not_started', 409)
        cancelled = self.store.action(record['id'], 'cancel', {}, 2, str(uuid.uuid4()))
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['elapsed_gpu_hours'], 0)
        self.assertEqual(self.store.preview(self.payload(start_at=record['start_at'], end_at=record['end_at'], gpu_count=4))['conflicts'], [])
        self.assertError(lambda: self.store.update(record['id'], {'notes': 'again'}, 3, str(uuid.uuid4())), 'record_closed', 409)
        events = self.store.history(record['id'])
        self.assertEqual([event['action'] for event in events], ['create', 'edit', 'cancel'])
        self.assertEqual(events[1]['before']['notes'], record['notes'])
        self.assertEqual(events[1]['after']['notes'], '夜间训练')

    def test_early_end_truncates_stats_and_releases_capacity(self):
        record = self.create(gpu_count=4)
        self.now += 2 * 3600
        ended = self.store.action(record['id'], 'end', {}, 1, str(uuid.uuid4()))
        self.assertEqual(ended['status'], 'ended')
        self.assertEqual(ended['elapsed_gpu_hours'], 8)
        self.now += 3 * DAY
        self.assertEqual(self.store.statistics(7)['people'][0]['gpu_hours'], 8)
        self.assertEqual(self.store.overview()['active_gpu_count'], 0)
        with self.store.usage.connect() as db:
            db.execute('UPDATE usage_accounts SET observed=?', (self.now,))
        self.assertEqual(self.store.preview(self.payload(start_at=iso(self.now), end_at=iso(self.now + DAY), gpu_count=4))['conflicts'], [])

    def test_released_overlap_clears_current_badge_but_preserves_history(self):
        long_record = self.create(gpu_indices=[0, 1], end_at=iso(self.t0 + 30 * DAY))
        overlap = self.create(gpu_indices=[0, 1], wechat='li_gpu', conflict_ack=True)
        self.assertTrue(self.store.get(long_record['id'])['has_conflict'])
        self.now += 3600
        self.store.action(overlap['id'], 'end', {}, 1, str(uuid.uuid4()))
        self.assertFalse(self.store.get(long_record['id'])['has_conflict'])
        history = self.store.history(overlap['id'])
        self.assertEqual(history[0]['after']['acknowledged_conflict_ids'], [long_record['id']])
        self.assertTrue(history[0]['after']['conflict_acknowledged'])
        self.assertEqual(len(history), 2)

    def test_expired_extension_preserves_gap_and_does_not_duplicate_hours(self):
        record = self.create(end_at=iso(self.t0 + 3600))
        self.now += 2 * 3600
        self.assertEqual(self.store.get(record['id'])['status'], 'expired')
        renewed = self.store.action(record['id'], 'extend', {'end_at': iso(self.t0 + 4 * 3600)}, 1, str(uuid.uuid4()))
        self.assertEqual(renewed['status'], 'active')
        self.assertEqual(len(renewed['registration_periods']), 2)
        self.now += 3600
        self.assertEqual(self.store.get(record['id'])['elapsed_gpu_hours'], 4)
        self.assertEqual(self.store.statistics(7)['people'][0]['gpu_hours'], 4)
        self.assertEqual([event['action'] for event in self.store.history(record['id'])], ['create', 'extend'])
        self.assertEqual(len(self.store.list_records(history=True)), 1)

    def test_expired_renewal_gap_is_not_continuous_planned_time_or_a_conflict(self):
        record = self.create(gpu_indices=[0, 1])
        # Another person's historical booking is entirely in the unregistered gap.
        self.create(gpu_indices=[0, 1], start_at=iso(self.t0 + 2 * DAY),
                    end_at=iso(self.t0 + 9 * DAY), wechat='li_gpu')
        self.now += 11 * DAY
        payload = {'end_at': iso(self.t0 + 12 * DAY)}
        self.assertEqual(self.store.preview(payload, record['id'])['conflicts'], [])
        renewed = self.store.action(record['id'], 'extend', payload, 1, str(uuid.uuid4()))
        self.assertEqual(renewed['continuous_days'], 1)
        self.assertEqual(renewed['registered_plan_days'], 2)
        self.assertEqual(renewed['planned_span_days'], 12)
        self.assertEqual(renewed['planned_gpu_hours'], 96)
        self.assertFalse(renewed['long_term'])
        self.assertFalse(renewed['has_conflict'])
        updated = self.store.update(record['id'], {'notes': '第二阶段'}, 2, str(uuid.uuid4()))
        self.assertEqual(updated['continuous_days'], 1)
        self.assertFalse(updated['has_conflict'])
        self.now += DAY
        own = next(p for p in self.store.statistics(30)['people'] if p['wechat'] == 'zhang_gpu')
        self.assertEqual(own['gpu_hours'], 96)
        self.assertEqual(own['long_term_hours'], 0)

    def test_long_term_statistics_exclude_later_short_renewal_after_gap(self):
        record = self.create(end_at=iso(self.t0 + 8 * DAY))
        self.now += 11 * DAY
        renewed = self.store.action(record['id'], 'extend', {'end_at': iso(self.t0 + 12 * DAY)}, 1, str(uuid.uuid4()))
        self.assertFalse(renewed['long_term'])  # The current renewed period is short.
        self.assertEqual(renewed['continuous_days'], 1)
        self.assertEqual(self.store.overview()['long_term_count'], 0)
        self.now += DAY
        own = self.store.statistics(7)['people'][0]
        self.assertEqual(own['gpu_hours'], 4 * 24 * 2)
        self.assertEqual(own['long_term_hours'], 3 * 24)
        self.assertEqual(own['long_term_gpu_hours'], 3 * 24 * 2)

    def test_active_extension_merges_coverage_and_rechecks_conflicts(self):
        record = self.create(gpu_indices=[0, 1], end_at=iso(self.t0 + 3600))
        later = self.create(gpu_indices=[0, 1], start_at=iso(self.t0 + 3600), end_at=iso(self.t0 + 7200), wechat='li_gpu')
        self.now += 1800
        payload = {'end_at': iso(self.t0 + 7200)}
        error = self.assertError(lambda: self.store.action(record['id'], 'extend', payload, 1, str(uuid.uuid4())), 'conflict_confirmation_required', 409)
        self.assertEqual(error.details['conflicts'][0]['id'], later['id'])
        extended = self.store.action(record['id'], 'extend', dict(payload, conflict_ack=True), 1, str(uuid.uuid4()))
        self.assertEqual(len(extended['registration_periods']), 1)
        self.now += 3600
        own = next(person for person in self.store.statistics(7)['people'] if person['wechat'] == 'zhang_gpu')
        self.assertEqual(own['gpu_hours'], 3)

    def test_person_overlap_dedup_and_different_machine_sum(self):
        end = iso(self.t0 + 2 * 3600)
        self.create(gpu_indices=[0, 1], wechat=' Person ', end_at=end)
        self.create(gpu_indices=[1, 2], wechat='person', end_at=end, conflict_ack=True)
        self.create(gpu_count=4, wechat='PERSON', end_at=end, conflict_ack=True)
        self.create(machine_id='10.20.6.38', gpu_count=1, gpu_indices=[0], wechat='person', end_at=end)
        self.now += 2 * 3600
        stats = self.store.statistics(7)
        self.assertEqual(len(stats['people']), 1)
        self.assertEqual(stats['people'][0]['booking_count'], 4)
        self.assertEqual(stats['people'][0]['gpu_hours'], 10)
        self.assertIn('not measured GPU compute-hours', stats['method']['interpretation'])

    def test_explicit_union_is_larger_than_largest_single_booking(self):
        self.create(gpu_indices=[0, 1])
        self.create(gpu_indices=[2, 3])
        self.now += 3600
        self.assertEqual(self.store.statistics(7)['people'][0]['gpu_hours'], 4)

    def test_statistics_clip_window_and_cancelled_future_has_no_hours(self):
        self.create(start_at=iso(self.t0 - 20 * DAY), end_at=iso(self.t0 + 10 * DAY), gpu_count=2)
        future = self.create(machine_id='10.20.1.21', start_at=iso(self.t0 + DAY), end_at=iso(self.t0 + 2 * DAY), wechat='future')
        self.store.action(future['id'], 'cancel', {}, 1, str(uuid.uuid4()))
        people = {person['wechat']: person for person in self.store.statistics(7)['people']}
        self.assertEqual(people['zhang_gpu']['gpu_hours'], 7 * 24 * 2)
        self.assertEqual(people['zhang_gpu']['long_term_hours'], 7 * 24)
        self.assertEqual(people['future']['gpu_hours'], 0)
        self.assertEqual(people['future']['booking_count'], 1)
        self.assertError(lambda: self.store.statistics(True))
        self.assertError(lambda: self.store.statistics(14))

    def test_overview_and_error_paths(self):
        self.create(end_at=iso(self.t0 + 12 * 3600))
        self.create(machine_id='10.20.6.38', start_at=iso(self.t0 + DAY), end_at=iso(self.t0 + 31 * DAY))
        overview = self.store.overview()
        self.assertEqual(overview['total_gpus'], 32)
        self.assertEqual(overview['active_gpu_count'], 2)
        self.assertEqual(overview['long_term_count'], 1)
        self.assertEqual(overview['expiring_count'], 1)
        self.assertEqual(overview['valid_reservations_count'], 2)
        self.assertEqual(overview['machines']['10.20.6.38']['valid_count'], 1)
        self.assertError(lambda: self.store.get('missing'), 'not_found', 404)
        self.assertError(lambda: self.store.history('missing'), 'not_found', 404)
        self.assertError(lambda: self.store.create(self.payload(), 'bad-key'))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
