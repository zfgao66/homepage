#!/usr/bin/env python3
"""Read-only Linux resource sampling and authenticated HTTPS reporting."""
import argparse
import csv
import datetime
import io
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def cpu_times():
    with open('/proc/stat', encoding='ascii') as stream:
        values = [int(value) for value in stream.readline().split()[1:9]]
    return sum(values), values[3] + values[4]


def parse_gpu_csv(text):
    def numeric(value):
        try:
            result = float(value.strip())
            return result if 0 <= result < float('inf') else None
        except ValueError:
            return None
    gpus = []
    fields = ['utilization_pct', 'memory_used_mib', 'memory_total_mib', 'temperature_c', 'power_w']
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 7:
            raise ValueError('Unexpected GPU fields')
        gpu = {'index': int(row[0].strip()), 'name': row[1].strip()}
        gpu.update(zip(fields, (numeric(value) for value in row[2:])))
        gpus.append(gpu)
    return gpus


def parse_cpu_info(text):
    records = []
    for block in re.split(r'\n\s*\n', text.strip()):
        record = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        record = {key.strip(): value.strip() for key, value in record.items()}
        if 'processor' in record:
            records.append(record)
    models = list(dict.fromkeys(record.get('model name') or record.get('Processor') for record in records))
    models = [model for model in models if model]
    have_core_ids = records and all(record.get('physical id') is not None and record.get('core id') is not None for record in records)
    physical = len({(record['physical id'], record['core id']) for record in records}) if have_core_ids else None
    sockets = len({record['physical id'] for record in records}) if records and all('physical id' in record for record in records) else None
    return {'cpu_model': ' / '.join(models)[:200] or None,
            'cpu_physical_cores': physical, 'cpu_logical_cpus': len(records) or os.cpu_count(),
            'cpu_sockets': sockets}


def local_mounts(text):
    candidates = []
    allowed = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'f2fs', 'jfs', 'reiserfs', 'zfs'}
    for line in text.splitlines():
        parts = line.split()
        try:
            separator = parts.index('-')
            filesystem, source = parts[separator + 1:separator + 3]
            mount = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), parts[4])
            if not mount.startswith('/') or len(mount) > 240 or mount == '/boot' or mount.startswith('/boot/'):
                continue
            if filesystem not in allowed and not (filesystem == 'overlay' and mount == '/'):
                continue
            if not source.startswith('/dev/') and filesystem not in ('zfs', 'overlay'):
                continue
            if 'ro' in parts[5].split(',') and mount != '/':
                continue
            candidates.append({'mountpoint': mount, 'filesystem': filesystem, 'identity': parts[2]})
        except (ValueError, IndexError):
            continue
    seen, result = set(), []
    for mount in sorted(candidates, key=lambda item: (item['mountpoint'] != '/', len(item['mountpoint']), item['mountpoint'])):
        if mount['identity'] not in seen:
            seen.add(mount['identity'])
            result.append(mount)
    return result[:32]


def collect_disks(mountinfo=pathlib.Path('/proc/self/mountinfo'), statvfs=os.statvfs):
    try:
        mounts = local_mounts(mountinfo.read_text())
    except OSError:
        return [], 'query_failed'
    disks, failures = [], 0
    for mount in mounts:
        disk = {key: mount[key] for key in ('mountpoint', 'filesystem')}
        disk.update(total_mib=None, used_mib=None, available_mib=None)
        try:
            info = statvfs(mount['mountpoint'])
            block_size = info.f_frsize or info.f_bsize
            if block_size <= 0 or info.f_blocks <= 0:
                raise ValueError('Invalid filesystem size')
            disk.update(total_mib=round(info.f_blocks * block_size / 1048576, 1),
                        used_mib=round(max(0, info.f_blocks - info.f_bfree) * block_size / 1048576, 1),
                        available_mib=round(max(0, info.f_bavail) * block_size / 1048576, 1))
        except (OSError, ValueError):
            failures += 1
        disks.append(disk)
    error = 'query_failed' if not disks or failures == len(disks) else 'partial' if failures else None
    return disks, error


def snapshot(machine_id):
    total_before, idle_before = cpu_times()
    time.sleep(0.3)
    total_after, idle_after = cpu_times()
    elapsed = total_after - total_before
    cpu = max(0, min(100, 100 * (1 - (idle_after - idle_before) / elapsed))) if elapsed > 0 else None
    memory = {}
    with open('/proc/meminfo', encoding='ascii') as stream:
        for line in stream:
            key, value = line.split(':', 1)
            memory[key] = int(value.strip().split()[0])
    available = memory.get('MemAvailable', memory.get('MemFree', 0))
    result = {
        'machine_id': machine_id,
        'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'cpu_percent': round(cpu, 1) if cpu is not None else None,
        'memory_used_mib': round(max(0, memory['MemTotal'] - available) / 1024, 1),
        'memory_total_mib': round(memory['MemTotal'] / 1024, 1),
        'gpus': [], 'gpu_error': None,
    }
    try:
        result.update(parse_cpu_info(pathlib.Path('/proc/cpuinfo').read_text()))
    except OSError:
        result.update(cpu_model=None, cpu_physical_cores=None, cpu_logical_cpus=os.cpu_count(), cpu_sockets=None)
    result['disks'], result['disk_error'] = collect_disks()
    binary = shutil.which('nvidia-smi')
    if not binary:
        result['gpu_error'] = 'not_installed'
        return result
    result['gpus'], result['gpu_error'] = collect_gpus(binary)
    return result


def query_gpus(binary, pci_bus_id=None):
    command = [binary, '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw',
               '--format=csv,noheader,nounits']
    if pci_bus_id:
        command.append('--id=' + pci_bus_id)
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             timeout=3 if pci_bus_id else 10, check=True)
    return parse_gpu_csv(process.stdout)


def collect_gpus(binary, device_root=pathlib.Path('/proc/driver/nvidia/gpus')):
    try:
        return query_gpus(binary), None
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    cards = []
    for info_path in sorted(device_root.glob('*/information'))[:32]:
        bus = info_path.parent.name
        try:
            found = query_gpus(binary, bus)
            if len(found) != 1:
                raise ValueError('Unexpected device count')
            card = found[0]
            card['pci_bus_id'] = bus
        except (subprocess.SubprocessError, ValueError, OSError):
            model = 'NVIDIA GPU'
            try:
                for line in info_path.read_text().splitlines():
                    if line.startswith('Model:'):
                        model = line.split(':', 1)[1].strip() or model
            except OSError:
                pass
            card = dict(index=None, name=model, pci_bus_id=bus, utilization_pct=None,
                        memory_used_mib=None, memory_total_mib=None, temperature_c=None, power_w=None)
        cards.append(card)
    return cards, 'query_failed'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upload(payload, endpoint, token):
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('HTTPS endpoint required')
    request = urllib.request.Request(endpoint, data=json.dumps(payload, allow_nan=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'User-Agent': 'RUC-Nova-Machines/1.0 (+http://gaozefeng.tech/machines/)'}, method='POST')
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('Upload rejected')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--machine-id', default=os.environ.get('MACHINE_ID'))
    parser.add_argument('--once', action='store_true', help='Print one snapshot without uploading')
    parser.add_argument('--send-once', action='store_true')
    args = parser.parse_args()
    if not args.machine_id:
        parser.error('machine id is required')
    if args.once:
        print(json.dumps(snapshot(args.machine_id), allow_nan=False))
        return
    endpoint, token = os.environ.get('MACHINES_INGEST_URL'), os.environ.get('MACHINES_AGENT_TOKEN')
    if not endpoint or not token:
        parser.error('reporting configuration is missing')
    interval = max(15, int(os.environ.get('MACHINES_INTERVAL', '30')))
    failures = 0
    while True:
        try:
            upload(snapshot(args.machine_id), endpoint, token)
            failures = 0
            if args.send_once:
                print('Upload verified')
                return
        except Exception as exc:
            failures = min(5, failures + 1)
            status = ' (' + str(exc.code) + ')' if isinstance(exc, urllib.error.HTTPError) else ''
            print('Report failed: ' + type(exc).__name__ + status, file=sys.stderr, flush=True)
            if args.send_once:
                raise SystemExit(1)
        delay = min(300, interval * 2 ** failures)
        time.sleep(delay + random.uniform(-2, 2))


if __name__ == '__main__':
    main()
