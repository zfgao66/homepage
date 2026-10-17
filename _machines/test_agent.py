import importlib.util
import pathlib
import unittest
import tempfile
from unittest.mock import patch
import subprocess
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('agent', pathlib.Path(__file__).with_name('agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class ParsingTests(unittest.TestCase):
    def test_cpu_counts_physical_cores_without_counting_threads_twice(self):
        blocks = []
        for processor in range(8):
            blocks.append('processor: %d\nphysical id: %d\ncore id: %d\nmodel name: Example CPU' % (processor, processor // 4, processor % 2))
        cpu = agent.parse_cpu_info('\n\n'.join(blocks))
        self.assertEqual(cpu['cpu_physical_cores'], 4)
        self.assertEqual(cpu['cpu_logical_cpus'], 8)
        self.assertEqual(cpu['cpu_sockets'], 2)
        self.assertIsNone(agent.parse_cpu_info('processor: 0')['cpu_physical_cores'])

    def test_disks_exclude_virtual_mounts_and_do_not_double_count_bind_mounts(self):
        mounts = agent.local_mounts('''1 0 8:1 / / rw - ext4 /dev/sda1 rw
2 1 8:1 /data /bind rw - ext4 /dev/sda1 rw
3 1 0:2 / /run rw - tmpfs tmpfs rw
4 1 8:2 / /boot rw - ext4 /dev/sda2 rw
5 1 259:1 / /data\\040disk rw - xfs /dev/nvme0n1p1 rw
6 1 0:3 / /network rw - nfs host:/export rw''')
        self.assertEqual([disk['mountpoint'] for disk in mounts], ['/', '/data disk'])

    def test_disk_available_space_uses_unprivileged_availability_and_preserves_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'mountinfo'
            path.write_text('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n2 1 8:2 / /data rw - xfs /dev/sda2 rw')
            def stat(mount):
                if mount == '/data':
                    raise OSError('unreadable')
                return SimpleNamespace(f_blocks=100, f_frsize=1048576, f_bsize=4096, f_bfree=40, f_bavail=35)
            disks, error = agent.collect_disks(path, stat)
            self.assertEqual(error, 'partial')
            self.assertEqual(disks[0]['total_mib'], 100)
            self.assertEqual(disks[0]['used_mib'], 60)
            self.assertEqual(disks[0]['available_mib'], 35)
            self.assertIsNone(disks[1]['available_mib'])

    def test_one_broken_card_does_not_hide_other_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            for bus in ['0000:19:00.0', '0000:1a:00.0']:
                path = root / bus / 'information'
                path.parent.mkdir()
                path.write_text('Model: NVIDIA GeForce RTX 3090\n')
            def query(binary, bus=None):
                if bus != '0000:1a:00.0':
                    raise subprocess.CalledProcessError(1, 'nvidia-smi')
                return agent.parse_gpu_csv('1, NVIDIA GeForce RTX 3090, 50, 12000, 24000, 60, 200')
            with patch.object(agent, 'query_gpus', side_effect=query):
                cards, error = agent.collect_gpus('nvidia-smi', root)
            self.assertEqual(error, 'query_failed')
            self.assertEqual(len(cards), 2)
            self.assertIsNone(cards[0]['index'])
            self.assertIsNone(cards[0]['utilization_pct'])
            self.assertEqual(cards[1]['utilization_pct'], 50)

    def test_nvidia_unknown_is_not_idle(self):
        gpu = agent.parse_gpu_csv('0, NVIDIA H100 80GB HBM3, [N/A], 1024, 81559, 38, 72.25\n')[0]
        self.assertIsNone(gpu['utilization_pct'])
        self.assertEqual(gpu['memory_total_mib'], 81559)
        self.assertEqual(gpu['power_w'], 72.25)

    def test_multiple_cards_and_quoted_name(self):
        cards = agent.parse_gpu_csv('0, "NVIDIA GPU, test", 0, 0, 24000, 35, 10\n1, NVIDIA RTX 4090, 96, 22000, 24000, 70, 300\n')
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]['name'], 'NVIDIA GPU, test')
        self.assertEqual(cards[1]['utilization_pct'], 96)

    def test_partial_row_is_rejected(self):
        with self.assertRaises(ValueError):
            agent.parse_gpu_csv('0, NVIDIA GPU, 0')

    def test_negative_and_nan_metrics_are_unknown(self):
        gpu = agent.parse_gpu_csv('0, GPU, nan, -1, 24000, N/A, inf')[0]
        self.assertIsNone(gpu['utilization_pct'])
        self.assertIsNone(gpu['memory_used_mib'])
        self.assertIsNone(gpu['power_w'])


if __name__ == '__main__':
    unittest.main()
