"""Read-only GPU process ownership samples. Used by the existing read-only agent."""
import csv
import io
import os
import pwd
import pathlib
import subprocess
import time


def sample_gpu_processes(binary='nvidia-smi', run=subprocess.run, proc_root='/proc'):
    def query(fields):
        result = run([binary, fields, '--format=csv,noheader,nounits'],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                     timeout=12, check=True)
        return list(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))
    query_started = time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, 'CLOCK_BOOTTIME') else None
    try:
        devices = query('--query-gpu=index,uuid')
        indices = {row[1].strip(): int(row[0].strip()) for row in devices if len(row) == 2}
        if len(indices) != len(devices):
            raise ValueError('incomplete device list')
        rows = query('--query-compute-apps=gpu_uuid,pid,used_memory')
        if len(rows) > 1024:
            raise ValueError('process list too large')
    except (OSError, subprocess.SubprocessError, ValueError, csv.Error):
        return {'complete': False, 'error': 'query_failed', 'processes': []}
    processes, unresolved = [], 0
    for row in rows:
        try:
            if len(row) != 3:
                raise ValueError('invalid process row')
            uuid, pid = row[0].strip(), int(row[1].strip())
            if uuid not in indices or pid <= 0:
                raise ValueError('unmapped device or PID')
            directory = os.path.join(proc_root, str(pid))
            uid = os.stat(directory).st_uid
            stat_file = pathlib.Path(directory, 'stat')
            if query_started is not None and stat_file.exists():
                # /proc stat field 22 follows the parenthesized process name.
                start_ticks = int(stat_file.read_text().rsplit(')', 1)[1].split()[19])
                if start_ticks / os.sysconf('SC_CLK_TCK') > query_started:
                    raise ValueError('PID reused after query began')
            username = pwd.getpwuid(uid).pw_name
            # Container/MPS ownership cannot safely identify the submitting person.
            cgroup = pathlib.Path(directory, 'cgroup')
            comm = pathlib.Path(directory, 'comm')
            if cgroup.exists() and any(tag in cgroup.read_text() for tag in ('docker', 'kubepods', 'containerd')):
                raise ValueError('container ownership unresolved')
            if comm.exists() and 'nvidia-cuda-mps' in comm.read_text():
                raise ValueError('MPS ownership unresolved')
            try:
                memory_mib = float(row[2].strip())
                if not 0 <= memory_mib < float('inf'):
                    memory_mib = None
            except ValueError:
                memory_mib = None
            processes.append({'gpu_index': indices[uuid], 'gpu_uuid': uuid,
                              'pid': pid, 'uid': uid, 'username': username,
                              'memory_mib': memory_mib})
        except (OSError, KeyError, ValueError, IndexError):
            unresolved += 1
    return {'gpu_indices': sorted(indices.values()), 'complete': unresolved == 0, 'error': None if not unresolved else 'unresolved_process',
            'processes': processes, 'unresolved_count': unresolved}
