"""Bounded, persistent sampled GPU occupancy. Never measures compute efficiency."""
import datetime as dt
import contextlib
import json
import math
import re
import sqlite3
import time

RETENTION_DAYS = 35
USERNAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.@-]{0,63}\Z')
MAX_USERNAMES = 10


def parse_usernames(value):
    """One or more SSH names, separated only by ordinary spaces."""
    if not isinstance(value, str) or len(value) > MAX_USERNAMES * 65 - 1:
        raise ValueError('最多填写 10 个服务器账号，每个账号不超过 64 个字符。')
    names = re.split(r' +', value.strip(' '))
    if not names[0] or any(not USERNAME.fullmatch(name) for name in names):
        raise ValueError('请只填写 SSH 账号名，多个账号用英文空格分隔，例如 zfgao wkli；不要填写逗号、中文、密码或换行。')
    if len(names) > MAX_USERNAMES:
        raise ValueError('最多填写 10 个服务器账号。')
    if len(set(names)) != len(names):
        raise ValueError('服务器账号不能重复填写。')
    return names


def segment_uids(segment):
    """Read the multi-account binding and the original single-UID history."""
    if 'uids' in segment:
        return segment['uids']
    return [segment['uid']] if segment.get('uid') is not None else None


def seconds(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class UsageStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS usage_accounts(machine TEXT, username TEXT, uid INTEGER, observed REAL,
                  PRIMARY KEY(machine,username));
                CREATE TABLE IF NOT EXISTS usage_last(machine TEXT PRIMARY KEY, at REAL, boot TEXT, owners TEXT, complete INTEGER);
                CREATE TABLE IF NOT EXISTS usage_intervals(machine TEXT, start REAL, end REAL, owners TEXT,
                  PRIMARY KEY(machine,start));
                CREATE INDEX IF NOT EXISTS usage_interval_time ON usage_intervals(machine,end);
            ''')
        self.last_cleanup = 0

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ingest(self, snapshot):
        sample = snapshot.get('gpu_usage')
        if not isinstance(sample, dict):
            return
        machine, at, now = snapshot['machine_id'], seconds(snapshot['observed_at']), self.clock()
        if not math.isfinite(at) or not -30 <= now - at <= 120:
            return
        accounts = sample.get('accounts', [])
        if not isinstance(accounts, list) or len(accounts) > 4096:
            return
        owners = sample.get('owners', {})
        complete = sample.get('complete') is True and isinstance(owners, dict) and len(owners) <= 32
        clean = {}
        try:
            for gpu, uids in owners.items():
                index = int(gpu)
                if not 0 <= index < 32 or not isinstance(uids, list) or len(uids) > 4096:
                    raise ValueError()
                if any(type(u) is not int or not 0 <= u <= 4294967295 for u in uids):
                    raise ValueError()
                clean[str(index)] = sorted(set(uids))
        except (ValueError, TypeError):
            complete = False
        encoded = json.dumps(clean, sort_keys=True, separators=(',', ':'))
        boot = str(sample.get('boot_id', ''))[:80]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prev = db.execute('SELECT at,boot,owners,complete FROM usage_last WHERE machine=?', (machine,)).fetchone()
            if prev and at <= prev[0]:
                return
            for account in accounts:
                if (isinstance(account, dict) and isinstance(account.get('username'), str)
                    and USERNAME.fullmatch(account['username']) and type(account.get('uid')) is int and account['uid'] >= 0):
                    db.execute('INSERT OR REPLACE INTO usage_accounts VALUES (?,?,?,?)', (machine, account['username'], account['uid'], at))
            catalog = {a['username']: a['uid'] for a in accounts if isinstance(a, dict) and isinstance(a.get('username'), str) and type(a.get('uid')) is int and a['uid'] >= 0 and USERNAME.fullmatch(a['username'])}
            # A pending account binds only from the first verified sample onward.
            records = db.execute('SELECT id,data FROM reservations').fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='reservations'").fetchone() else []
            for record_id, raw in records:
                record = json.loads(raw)
                segments = record.get('usage_segments', [])
                if (record.get('machine_id') != machine or not segments or segment_uids(segments[-1]) is not None
                    or record.get('ended_at') or record.get('cancelled_at') or seconds(record['end_at']) <= at):
                    continue
                try:
                    usernames = parse_usernames(record.get('server_username'))
                except ValueError:
                    continue
                if any(username not in catalog for username in usernames):
                    continue
                before = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                segments[-1]['until'] = at
                bound = dict(segments[-1], since=at, until=None, uids=sorted({catalog[name] for name in usernames}))
                bound.pop('uid', None)
                segments.append(bound)
                record['version'] += 1
                stamp = dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat()
                record['updated_at'] = stamp
                after = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                db.execute('UPDATE reservations SET data=? WHERE id=?', (after, record_id))
                db.execute('INSERT INTO reservation_events(reservation_id,action,at,before_data,after_data) VALUES (?,?,?,?,?)', (record_id, 'account_verified', stamp, before, after))
            # Two complete samples on the same boot bracket each estimated interval.
            if prev and prev[3] and complete and boot and boot == prev[1] and 0 < at - prev[0] <= 90:
                last = db.execute('SELECT start,end,owners FROM usage_intervals WHERE machine=? ORDER BY start DESC LIMIT 1', (machine,)).fetchone()
                if last and last[1] == prev[0] and last[2] == prev[2] and at - last[0] <= 300:
                    db.execute('UPDATE usage_intervals SET end=? WHERE machine=? AND start=?', (at, machine, last[0]))
                else:
                    db.execute('INSERT INTO usage_intervals VALUES (?,?,?,?)', (machine, prev[0], at, prev[2]))
            db.execute('INSERT OR REPLACE INTO usage_last VALUES (?,?,?,?,?)', (machine, at, boot, encoded, int(complete)))
            if now - self.last_cleanup > 3600:
                db.execute('DELETE FROM usage_intervals WHERE end<?', (now - RETENTION_DAYS * 86400,))
                self.last_cleanup = now

    def resolve(self, db, machine, username, now):
        row = db.execute('SELECT uid,observed FROM usage_accounts WHERE machine=? AND username=?', (machine, username)).fetchone()
        health = db.execute('SELECT at FROM usage_last WHERE machine=?', (machine,)).fetchone()
        if not row or not -30 <= now - row[1] <= 600:
            if not health or now - health[0] > 600:
                return None
            raise ValueError(f'服务器账号“{username}”不存在或账号清单超过 10 分钟未更新，请检查账号名，或等待采样恢复后重试。')
        return row[0]

    def resolve_many(self, db, machine, usernames, now):
        uids = [self.resolve(db, machine, name, now) for name in parse_usernames(usernames)]
        # Do not present a partial account group as fully observed.
        return None if any(uid is None for uid in uids) else sorted(set(uids))

    def measure(self, allocations, lower, upper):
        """Union per account/machine; capacity uses explicit union or max count.

        allocations: (machine, uids or legacy uid, gpu_indices, count, start, end).
        Each sample interval uses the preceding observed owner set, with <=90s gaps.
        """
        if upper <= lower:
            return self.result(0, 0, 0)
        planned = covered = used = 0.0
        with self.connect() as db:
            hosts = sorted({a[0] for a in allocations})
            for host in hosts:
                own = [a for a in allocations if a[0] == host and a[4] < upper and a[5] > lower]
                own = [(a[0], None if a[1] is None else frozenset([a[1]] if isinstance(a[1], int) else a[1]), *a[2:]) for a in own]
                if not own:
                    continue
                cuts = sorted({lower, upper} | {max(lower, a[4]) for a in own} | {min(upper, a[5]) for a in own})
                intervals = db.execute('SELECT start,end,owners FROM usage_intervals WHERE machine=? AND end>? AND start<? ORDER BY start', (host, lower, upper)).fetchall()
                intervals = [(a, b, json.loads(o)) for a, b, o in intervals]
                for start, end in zip(cuts, cuts[1:]):
                    live = [a for a in own if a[4] < end and a[5] > start]
                    if not live or end <= start:
                        continue
                    explicit = {g for a in live for g in a[2]}
                    capacity = max(len(explicit), max(a[3] for a in live))
                    planned += (end - start) * capacity
                    if any(a[1] is None for a in live):
                        continue
                    for a, b, owners in intervals:
                        duration = min(b, end) - max(a, start)
                        if duration <= 0:
                            continue
                        covered += duration * capacity
                        occupied = {int(g) for g, uids in owners.items()
                                    if any(r[1].intersection(uids) and (not r[2] or int(g) in r[2]) for r in live)}
                        used += duration * min(capacity, len(occupied))
        return self.result(planned, covered, used)

    @staticmethod
    def result(planned, covered, used):
        return {'elapsed_registered_gpu_hours': round(planned / 3600, 6),
                'observed_registered_gpu_hours': round(covered / 3600, 6),
                'occupied_gpu_hours': round(used / 3600, 6) if covered > 0 else None,
                'usage_percent': round(used / covered * 100, 2) if covered > 0 else None,
                'coverage_percent': round(covered / planned * 100, 2) if planned > 0 else None,
                'retention_days': RETENTION_DAYS,
                'method': '采样占用卡时 ÷ 有数据覆盖的登记卡时；登记的多个账号合并，同卡同时占用去重。缺失数据不计为空闲，非计算效率。'}
