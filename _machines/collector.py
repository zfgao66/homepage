#!/usr/bin/env python3
"""Collect fresh read-only GPU snapshots through a restricted SSH gateway."""
import argparse
import concurrent.futures
import datetime
import json
import pathlib
import subprocess
import sys
import time

import agent
import status_api

HOSTS = ('10.20.6.38', '10.20.5.8', '10.20.5.73', '10.20.5.100',
         '10.20.5.51', '10.20.6.135', '10.20.1.21')


def parse_snapshot(text, machine_id, now=None):
    if len(text.encode()) > 65536:
        raise ValueError('Oversized snapshot')
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('machine_id') != machine_id:
        raise ValueError('Machine identity mismatch')
    if not isinstance(data.get('gpus'), list) or len(data['gpus']) > 32:
        raise ValueError('Invalid GPU list')
    observed = datetime.datetime.fromisoformat(data['observed_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None:
        raise ValueError('Snapshot time requires a timezone')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age = (now - observed).total_seconds()
    if age < -30 or age > 120:
        raise ValueError('Snapshot is not fresh')
    return data


def collect_and_upload(machine_id, token, endpoint, ssh_config, snapshot_store=None):
    try:
        process = subprocess.run(['/usr/bin/ssh', '-F', ssh_config, machine_id, 'gpu-snapshot'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=35, check=True)
        data = parse_snapshot(process.stdout, machine_id)
        if snapshot_store is not None:
            snapshot_store.publish(data)
        result = {'ip': machine_id, 'ok': True, 'gpus': len(data['gpus']),
                  'observed_at': data['observed_at']}
        try:
            agent.upload(data, endpoint, token)
        except Exception as error:
            if snapshot_store is None:
                raise
            # A secondary upload outage must not delay fresh local collection.
            result['upload_error'] = type(error).__name__
            if hasattr(error, 'code'):
                result['http_status'] = error.code
        return result
    except Exception as error:
        result = {'ip': machine_id, 'ok': False, 'error': type(error).__name__}
        if isinstance(error, subprocess.CalledProcessError):
            result['ssh_exit_code'] = error.returncode
        if hasattr(error, 'code'):
            result['http_status'] = error.code
        return result


def run_cycle(config, selected=None, snapshot_store=None):
    hosts = selected if selected is not None else config['tokens']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(collect_and_upload, ip, config['tokens'][ip],
                              config['endpoint'], config['ssh_config'], snapshot_store) for ip in hosts]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    return results


class StatusReporter:
    """Keep only current health in memory and avoid logging every sample."""

    def __init__(self, heartbeat_seconds=3600):
        self.states = {}
        self.last_heartbeat = None
        self.heartbeat_seconds = heartbeat_seconds

    def report(self, results, now=None):
        now = time.monotonic() if now is None else now
        for result in results:
            ip = result['ip']
            state = tuple(result.get(key) for key in
                          ('ok', 'gpus', 'error', 'ssh_exit_code', 'http_status', 'upload_error'))
            if self.states.get(ip) != state:
                print(json.dumps(result), flush=True)
                self.states[ip] = state
        if self.last_heartbeat is None:
            self.last_heartbeat = now
        elif now - self.last_heartbeat >= self.heartbeat_seconds:
            print(json.dumps({'event': 'collector_health',
                              'online': sorted(ip for ip, state in self.states.items() if state[0]),
                              'unavailable': sorted(ip for ip, state in self.states.items() if not state[0])}),
                  flush=True)
            self.last_heartbeat = now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=pathlib.Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if set(config['tokens']) != set(HOSTS):
        raise ValueError('Expected the seven approved machines')
    interval = max(15, int(config.get('interval', 30)))
    if args.once:
        results = run_cycle(config)
        for result in results:
            print(json.dumps(result), flush=True)
        raise SystemExit(0 if all(item['ok'] for item in results) else 1)
    failures = dict.fromkeys(HOSTS, 0)
    next_due = dict.fromkeys(HOSTS, 0)
    reporter = StatusReporter()
    snapshot_store = None
    if config.get('serve_status'):
        snapshot_store = status_api.LatestSnapshots(HOSTS)
        status_api.start_server(snapshot_store, int(config['serve_status']['port']))
    while True:
        started = time.monotonic()
        selected = [ip for ip in HOSTS if next_due[ip] <= started]
        results = run_cycle(config, selected, snapshot_store)
        reporter.report(results)
        for result in results:
            ip = result['ip']
            failures[ip] = 0 if result['ok'] else min(5, failures[ip] + 1)
            delay = interval if result['ok'] else min(300, interval * 2 ** (failures[ip] - 1))
            next_due[ip] = started + delay
        time.sleep(max(1, interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
