"""Persistent coordination records, independent of telemetry and GPU processes.

Intervals are half-open. Statistics describe *registered* time, not computation:
within each person's machine, overlapping records count the larger of the union
of explicitly selected GPUs and the largest concurrent requested GPU count.
Different machines add together. Historical edits use the latest agreed plan;
the immutable event log preserves each earlier plan for inspection.
"""
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import sqlite3
import time
import unicodedata
import uuid


HOSTS = {'10.20.6.38': 4, '10.20.5.8': 4, '10.20.5.73': 4,
         '10.20.5.100': 4, '10.20.5.51': 4, '10.20.6.135': 4,
         '10.20.1.21': 8}
TEXT_LIMITS = {'name': 80, 'wechat': 80, 'purpose': 500,
               'notes': 4000, 'usage_pattern': 300}
EDITABLE = frozenset((*TEXT_LIMITS, 'machine_id', 'gpu_count', 'gpu_indices',
                      'start_at', 'end_at'))
DAY = 86400


class ReservationError(Exception):
    def __init__(self, message, status=400, code='validation_error', details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or {}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _seconds(value, field='时间'):
    if not isinstance(value, str) or len(value) > 64:
        raise ReservationError(field + '必须是包含时区的 ISO 时间')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        result = parsed.timestamp()
        # A finite, representable range prevents platform/round-trip overflow.
        if not math.isfinite(result) or not 946684800 <= result < 4102444800:
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError):
        raise ReservationError(field + '必须是 2000—2099 年内包含时区的有效时间') from None


def _identity(record):
    return unicodedata.normalize('NFKC', record['wechat']).strip().casefold()


def _effective_end(record):
    end = _seconds(record['end_at'])
    if record.get('cancelled_at'):
        return _seconds(record['start_at'])
    if record.get('ended_at'):
        return max(_seconds(record['start_at']), min(end, _seconds(record['ended_at'])))
    return end


def _status(record, now):
    if record.get('cancelled_at'):
        return 'cancelled'
    if record.get('ended_at'):
        return 'ended'
    if now >= _seconds(record['end_at']):
        return 'expired'
    return 'scheduled' if now < _seconds(record['start_at']) else 'active'


def _plan_periods(record):
    """Planned coverage includes its original plan after an early release."""
    start, end = _seconds(record['start_at']), _seconds(record['end_at'])
    periods = record.get('registration_periods', [[record['start_at'], record['end_at']]])
    return [(max(start, _seconds(a)), min(end, _seconds(b))) for a, b in periods
            if min(end, _seconds(b)) > max(start, _seconds(a))]


def _periods(record):
    """Effective coverage excludes expiry gaps and time after release."""
    end = _effective_end(record)
    return [(a, min(b, end)) for a, b in _plan_periods(record) if min(b, end) > a]


def _long_term(record):
    periods = _plan_periods(record)
    return bool(periods and periods[-1][1] - periods[-1][0] > 7 * DAY)


def _long_coverage(record):
    return dict(record, registration_periods=[[_iso(a), _iso(b)] for a, b in _plan_periods(record) if b - a > 7 * DAY])


def _extended_periods(before, new_end, now):
    periods = _periods(before)
    extension_start = max(_seconds(before['end_at']), now)
    if periods and periods[-1][1] >= extension_start:
        periods[-1] = (periods[-1][0], new_end)
    else:
        periods.append((extension_start, new_end))
    return [[_iso(a), _iso(b)] for a, b in periods]


def _change_periods(before, candidate, now):
    if candidate['start_at'] != before['start_at']:
        return [[candidate['start_at'], candidate['end_at']]]
    if _seconds(candidate['end_at']) > _seconds(before['end_at']):
        return _extended_periods(before, _seconds(candidate['end_at']), now)
    return [[_iso(a), _iso(b)] for a, b in _plan_periods(dict(before, **candidate))]


def _normalize(payload, base=None):
    if not isinstance(payload, dict):
        raise ReservationError('申请信息必须是对象')
    unknown = set(payload) - EDITABLE - {'conflict_ack'}
    if unknown:
        raise ReservationError('包含不支持的申请字段', details={'fields': sorted(unknown)})
    if 'conflict_ack' in payload and type(payload['conflict_ack']) is not bool:
        raise ReservationError('冲突确认必须为 true 或 false')
    result = {key: base[key] for key in EDITABLE if base and key in base}
    result.update({key: value for key, value in payload.items() if key in EDITABLE})
    if not isinstance(result.get('machine_id'), str) or result['machine_id'] not in HOSTS:
        raise ReservationError('请选择有效的服务器 IP')
    count = result.get('gpu_count')
    if type(count) is not int or not 1 <= count <= HOSTS[result['machine_id']]:
        raise ReservationError('GPU 数量超出该服务器的实际范围')
    indices = result.get('gpu_indices', [])
    if not isinstance(indices, list) or any(type(index) is not int for index in indices):
        raise ReservationError('GPU 编号必须是整数数组')
    if indices and (len(indices) != count or len(set(indices)) != len(indices)
                    or any(index < 0 or index >= HOSTS[result['machine_id']] for index in indices)):
        raise ReservationError('指定的 GPU 编号须有效、无重复，且数量与申请数量一致')
    result['gpu_indices'] = sorted(indices)
    for field, limit in TEXT_LIMITS.items():
        value = result.get(field, '')
        if not isinstance(value, str):
            raise ReservationError(field + '必须是文本')
        value = unicodedata.normalize('NFC', value).strip()
        if len(value) > limit or any(unicodedata.category(c) == 'Cc' and c not in '\n\t' for c in value):
            raise ReservationError(field + '过长或含有无效控制字符', details={'field': field, 'max_length': limit})
        if field in ('name', 'wechat') and (not value or '\n' in value or '\t' in value):
            raise ReservationError('姓名和微信号必填，且不能换行')
        result[field] = value
    start = _seconds(result.get('start_at'), '开始时间')
    end = _seconds(result.get('end_at'), '结束时间')
    if end <= start:
        raise ReservationError('结束时间必须晚于开始时间')
    result['start_at'], result['end_at'] = _iso(start), _iso(end)
    return result


def _conflicts(candidate, records, exclude_id=None):
    start, end = _seconds(candidate['start_at']), _seconds(candidate['end_at'])
    proposed = _periods(candidate)
    overlapping = [record for record in records if record['id'] != exclude_id
                   and record['machine_id'] == candidate['machine_id']
                   and not record.get('cancelled_at')
                   and any(a < d and b > c for a, b in _periods(record) for c, d in proposed)]
    reasons = {}
    selected = set(candidate['gpu_indices'])
    for record in overlapping:
        if selected.intersection(record['gpu_indices']):
            reasons[record['id']] = 'gpu_overlap'
    # Compute concurrent capacity, never the sum of all bookings that merely
    # touch the proposed interval. Explicit duplicate cards are already warned
    # above and count once when estimating remaining capacity.
    points = {start, end, *(p for period in proposed for p in period)}
    for record in overlapping:
        for a, b in _periods(record):
            if a < end and b > start:
                points.update((max(start, a), min(end, b)))
    ordered = sorted(points)
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        if not any(a < right and b > left for a, b in proposed):
            continue
        live = [record for record in overlapping
                if any(a < right and b > left for a, b in _periods(record))]
        explicit = selected.copy()
        unspecified = candidate['gpu_count'] if not selected else 0
        for record in live:
            if record['gpu_indices']:
                explicit.update(record['gpu_indices'])
            else:
                unspecified += record['gpu_count']
        if len(explicit) + unspecified > HOSTS[candidate['machine_id']]:
            for record in live:
                reasons.setdefault(record['id'], 'capacity_exceeded')
    result = []
    for record in overlapping:
        if record['id'] in reasons:
            result.append({key: record[key] for key in ('id', 'name', 'wechat', 'machine_id', 'gpu_indices', 'gpu_count', 'start_at', 'end_at')}
                          | {'reason': reasons[record['id']], 'effective_end_at': _iso(_effective_end(record))})
    return sorted(result, key=lambda item: (item['start_at'], item['id']))


def _decorate(record, now, records=None):
    result = dict(record)
    start, end = _seconds(record['start_at']), _seconds(record['end_at'])
    effective = _effective_end(record)
    planned_periods = _plan_periods(record)
    planned_seconds = sum(b - a for a, b in planned_periods)
    continuous_seconds = planned_periods[-1][1] - planned_periods[-1][0] if planned_periods else 0
    elapsed = sum(max(0, min(now, b) - a) for a, b in _periods(record))
    remaining = sum(max(0, b - max(now, a)) for a, b in _periods(record))
    result.update(status=_status(record, now), long_term=_long_term(record),
                  continuous_days=round(continuous_seconds / DAY, 4),
                  registered_plan_days=round(planned_seconds / DAY, 4),
                  planned_span_days=round((end - start) / DAY, 4),
                  elapsed_days=round(elapsed / DAY, 4),
                  effective_end_at=_iso(effective),
                  planned_gpu_hours=round(planned_seconds * record['gpu_count'] / 3600, 4),
                  elapsed_gpu_hours=round(elapsed * record['gpu_count'] / 3600, 4),
                  remaining_gpu_hours=round(remaining * record['gpu_count'] / 3600, 4))
    # Public active badges describe unresolved current/future coordination.
    # Past overlaps remain in acknowledged IDs and immutable event snapshots.
    current_plan = dict(record, start_at=_iso(max(now, start)))
    conflicts = _conflicts(current_plan, records, record['id']) if records is not None and result['status'] in ('active', 'scheduled') else []
    result.update(conflicts=conflicts, has_conflict=bool(conflicts))
    return result


class ReservationStore:
    def __init__(self, path, clock=time.time):
        self.path = os.fspath(path)
        self.clock = clock
        if self.path == ':memory:':
            raise ValueError('A persistent SQLite file is required')
        pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reservation_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT NOT NULL REFERENCES reservations(id),
                    action TEXT NOT NULL, at TEXT NOT NULL,
                    before_data TEXT, after_data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reservation_events_by_id
                    ON reservation_events(reservation_id, sequence);
                CREATE TABLE IF NOT EXISTS reservation_requests (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    response TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout=10000')
            conn.execute('PRAGMA foreign_keys=ON')
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _records(conn):
        return [json.loads(row[0]) for row in conn.execute('SELECT data FROM reservations')]

    @staticmethod
    def _find(records, reservation_id):
        for record in records:
            if record['id'] == reservation_id:
                return record
        raise ReservationError('登记不存在', 404, 'not_found')

    @staticmethod
    def _version(record, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise ReservationError('修改时需要提供当前版本号')
        if record['version'] != expected_version:
            raise ReservationError('登记已被其他同学修改，请刷新后重试', 409, 'version_conflict',
                                   {'current_version': record['version']})

    @staticmethod
    def _check_open(record):
        if record.get('ended_at') or record.get('cancelled_at'):
            raise ReservationError('已结束或取消的登记不能继续修改', 409, 'record_closed')

    @staticmethod
    def _confirm(payload, conflicts):
        if conflicts and payload.get('conflict_ack') is not True:
            raise ReservationError('存在时间或 GPU 冲突，请先查看联系人并明确确认', 409,
                                   'conflict_confirmation_required', {'conflicts': conflicts})

    def _write(self, operation, reservation_id, payload, expected_version, request_id, apply):
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ReservationError('每次提交需要一个有效的请求 UUID') from None
        try:
            fingerprint = hashlib.sha256(_json([operation, reservation_id, payload, expected_version]).encode()).hexdigest()
        except (TypeError, ValueError):
            raise ReservationError('申请包含无效 JSON 数据') from None
        with self._connect() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                prior = conn.execute('SELECT fingerprint, response FROM reservation_requests WHERE request_id=?', (request_id,)).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise ReservationError('同一请求标识不能用于不同内容', 409, 'idempotency_conflict')
                    conn.execute('COMMIT')
                    return json.loads(prior[1])
                now = self.clock()
                records = self._records(conn)
                before, after = apply(records, now)
                conn.execute('INSERT INTO reservations(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                             (after['id'], _json(after)))
                conn.execute('INSERT INTO reservation_events(reservation_id,action,at,before_data,after_data) VALUES (?,?,?,?,?)',
                             (after['id'], operation, _iso(now), _json(before) if before else None, _json(after)))
                combined = [item for item in records if item['id'] != after['id']] + [after]
                response = _decorate(after, now, combined)
                conn.execute('INSERT INTO reservation_requests(request_id,fingerprint,response,created_at) VALUES (?,?,?,?)',
                             (request_id, fingerprint, _json(response), _iso(now)))
                conn.execute('COMMIT')
                return response
            except Exception:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
                raise

    def list_records(self, history=False, machine_id=None):
        if machine_id is not None and machine_id not in HOSTS:
            raise ReservationError('无效的服务器 IP')
        with self._connect() as conn:
            records = self._records(conn)
        now = self.clock()
        result = [_decorate(record, now, records) for record in records
                  if (machine_id is None or record['machine_id'] == machine_id)
                  and (history or _status(record, now) in ('active', 'scheduled'))]
        order = {'active': 0, 'scheduled': 1, 'expired': 2, 'ended': 3, 'cancelled': 4}
        return sorted(result, key=lambda item: (order[item['status']], item['start_at'], item['created_at'], item['id']))

    def get(self, reservation_id):
        with self._connect() as conn:
            records = self._records(conn)
        return _decorate(self._find(records, reservation_id), self.clock(), records)

    def history(self, reservation_id):
        with self._connect() as conn:
            self._find(self._records(conn), reservation_id)
            rows = conn.execute('SELECT sequence,action,at,before_data,after_data FROM reservation_events WHERE reservation_id=? ORDER BY sequence',
                                (reservation_id,)).fetchall()
        return [{'sequence': row[0], 'action': row[1], 'at': row[2],
                 'before': json.loads(row[3]) if row[3] else None, 'after': json.loads(row[4])} for row in rows]

    def preview(self, payload, exclude_id=None):
        with self._connect() as conn:
            records = self._records(conn)
        base = self._find(records, exclude_id) if exclude_id else None
        candidate = _normalize(payload, base)
        if base:
            candidate['registration_periods'] = _change_periods(base, candidate, self.clock())
        conflicts = _conflicts(candidate, records, exclude_id)
        return {'candidate': {key: candidate[key] for key in EDITABLE}, 'conflicts': conflicts,
                'requires_confirmation': bool(conflicts), 'long_term': _long_term(candidate)}

    def create(self, payload, request_id):
        def apply(records, now):
            candidate = _normalize(payload)
            conflicts = _conflicts(candidate, records)
            self._confirm(payload, conflicts)
            after = dict(candidate, id=str(uuid.uuid4()), created_at=_iso(now), updated_at=_iso(now),
                         version=1, last_extended_at=None, ended_at=None, cancelled_at=None,
                         registration_periods=[[candidate['start_at'], candidate['end_at']]],
                         conflict_acknowledged=bool(conflicts), acknowledged_conflict_ids=[c['id'] for c in conflicts])
            return None, after
        return self._write('create', None, payload, None, request_id, apply)

    def update(self, reservation_id, payload, expected_version, request_id):
        def apply(records, now):
            before = self._find(records, reservation_id)
            self._version(before, expected_version)
            self._check_open(before)
            candidate = _normalize(payload, before)
            candidate['registration_periods'] = _change_periods(before, candidate, now)
            conflicts = _conflicts(candidate, records, reservation_id)
            self._confirm(payload, conflicts)
            after = dict(before, **candidate)
            after.update(updated_at=_iso(now), version=before['version'] + 1,
                         conflict_acknowledged=bool(conflicts), acknowledged_conflict_ids=[c['id'] for c in conflicts])
            if _seconds(after['end_at']) > _seconds(before['end_at']):
                after['last_extended_at'] = _iso(now)
            return before, after
        return self._write('edit', reservation_id, payload, expected_version, request_id, apply)

    def action(self, reservation_id, action, payload, expected_version, request_id):
        if action not in ('extend', 'end', 'cancel'):
            raise ReservationError('不支持的操作')
        if not isinstance(payload, dict):
            raise ReservationError('操作内容必须是对象')
        allowed = {'end_at', 'conflict_ack'} if action == 'extend' else set()
        if set(payload) - allowed:
            raise ReservationError('该操作包含不支持的字段')
        def apply(records, now):
            before = self._find(records, reservation_id)
            self._version(before, expected_version)
            self._check_open(before)
            after = dict(before)
            if action == 'extend':
                candidate = _normalize(payload, before)
                if _seconds(candidate['end_at']) <= max(now, _seconds(before['end_at'])):
                    raise ReservationError('续期结束时间必须晚于原结束时间和当前时间')
                candidate['registration_periods'] = _change_periods(before, candidate, now)
                conflicts = _conflicts(candidate, records, reservation_id)
                self._confirm(payload, conflicts)
                after.update(candidate)
                after.update(last_extended_at=_iso(now), conflict_acknowledged=bool(conflicts),
                             acknowledged_conflict_ids=[c['id'] for c in conflicts])
            elif action == 'cancel':
                if now >= _seconds(before['start_at']):
                    raise ReservationError('已开始的登记请使用结束操作', 409, 'already_started')
                after['cancelled_at'] = _iso(now)
            else:
                if now < _seconds(before['start_at']):
                    raise ReservationError('尚未开始的登记请使用取消操作', 409, 'not_started')
                after['ended_at'] = _iso(now)
            after.update(updated_at=_iso(now), version=before['version'] + 1)
            return before, after
        return self._write(action, reservation_id, payload, expected_version, request_id, apply)

    def overview(self):
        records = self.list_records()
        now = self.clock()
        active = [r for r in records if r['status'] == 'active']
        return {'total_gpus': sum(HOSTS.values()), 'active_gpu_count': sum(r['gpu_count'] for r in active),
                'active_reservations_count': len(active), 'valid_reservations_count': len(records),
                'scheduled_reservations_count': len(records) - len(active),
                'long_term_count': sum(r['long_term'] for r in records),
                'active_long_term_count': sum(r['long_term'] for r in active),
                'expiring_count': sum(now < _seconds(r['end_at']) <= now + DAY for r in active),
                'as_of': _iso(now), 'expiring_within_hours': 24,
                'machines': {host: {'valid_count': sum(r['machine_id'] == host for r in records),
                                    'active_gpu_count': sum(r['gpu_count'] for r in active if r['machine_id'] == host)}
                             for host in HOSTS},
                'method_text': '当前登记卡数按正在使用的申请相加；冲突登记可能使数量超过库存。登记情况与实时 GPU 利用率分别统计。',
                'method': 'active_gpu_count sums active registrations and may exceed inventory when coordinated overlaps exist; it does not measure GPU utilization'}

    @staticmethod
    def _integrate(records, lower, upper):
        intervals = [(max(lower, start), min(upper, end), r) for r in records for start, end in _periods(r)]
        intervals = [(start, end, r) for start, end, r in intervals if end > start]
        points = sorted({p for start, end, _ in intervals for p in (start, end)})
        gpu_seconds = seconds = 0
        for left, right in zip(points, points[1:]):
            live = [r for start, end, r in intervals if start < right and end > left]
            if not live:
                continue
            selected = {index for r in live for index in r['gpu_indices']}
            count = max(len(selected), max(r['gpu_count'] for r in live))
            seconds += right - left
            gpu_seconds += (right - left) * count
        return gpu_seconds, seconds

    def statistics(self, days):
        if type(days) is not int or days not in (7, 30):
            raise ReservationError('统计范围只支持 7 天或 30 天')
        now = self.clock()
        lower = now - days * DAY
        with self._connect() as conn:
            records = self._records(conn)
        people = {}
        for record in records:
            created = lower <= _seconds(record['created_at']) <= now
            elapsed = _seconds(record['start_at']) < now and _effective_end(record) > lower and not record.get('cancelled_at')
            current = _status(record, now) in ('active', 'scheduled')
            if not (created or elapsed or current):
                continue
            key = _identity(record)
            people.setdefault(key, []).append(record)
        result = []
        for key, own in people.items():
            latest = max(own, key=lambda r: r['updated_at'])
            gpu_seconds = long_gpu_seconds = 0
            for host in HOSTS:
                matching = [r for r in own if r['machine_id'] == host]
                gpu_seconds += self._integrate(matching, lower, now)[0]
                long_gpu_seconds += self._integrate([_long_coverage(r) for r in matching], lower, now)[0]
            long_records = [_long_coverage(r) for r in own]
            long_seconds = self._integrate(long_records, lower, now)[1]
            active = [r for r in own if _status(r, now) == 'active']
            valid = [r for r in own if _status(r, now) in ('active', 'scheduled')]
            result.append({'person_key': key, 'name': latest['name'], 'wechat': latest['wechat'],
                           'booking_count': sum(lower <= _seconds(r['created_at']) <= now for r in own),
                           'gpu_hours': round(gpu_seconds / 3600, 4),
                           'active_reservations_count': len(active), 'valid_reservations_count': len(valid),
                           'active_gpu_count': sum(r['gpu_count'] for r in active),
                           'long_term_hours': round(long_seconds / 3600, 4),
                           'long_term_gpu_hours': round(long_gpu_seconds / 3600, 4),
                           'current_records': [_decorate(r, now) for r in valid]})
        result.sort(key=lambda row: (-row['gpu_hours'], row['person_key']))
        return {'days': days, 'from': _iso(lower), 'to': _iso(now), 'people': result,
                'total_gpu_hours': round(sum(row['gpu_hours'] for row in result), 4),
                'method_text': '统计登记时间，不代表实际计算用量。同一微信号在同一服务器的重叠申请，按指定显卡并集与最大申请张数中的较大值去重；不同服务器分别累加。申请次数按本时段内创建的登记统计，含后来取消的申请。提前结束和到期后的续期空档不累计卡时。',
                'method': {'time': 'elapsed registration intervals clipped to the trailing window; ended/cancelled intervals truncated; extensions update one record',
                           'person': 'WeChat identifier normalized with Unicode NFKC and casefold',
                           'overlap': 'per person and machine: max(explicit GPU union size, largest concurrent requested count); add different machines; different people remain separate',
                           'booking_count': 'records created within the window, including later-cancelled records',
                           'long_term_hours': 'union of elapsed wall-clock intervals from plans longer than seven days, across machines',
                           'interpretation': 'registered GPU card-hours, not measured GPU compute-hours'}}
