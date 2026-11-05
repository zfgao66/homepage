import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import process_sampling


class ProcessSamplingTests(unittest.TestCase):
    def test_owners_are_mapped_and_same_gpu_processes_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            for pid in ('123', '124'):
                pathlib.Path(tmp, pid).mkdir()
            results = iter([SimpleNamespace(stdout='0, GPU-a\n'),
                            SimpleNamespace(stdout='GPU-a, 123, 200\nGPU-a, 124, 300\n')])
            with patch.object(process_sampling.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='wkli')):
                sample = process_sampling.sample_gpu_processes(run=lambda *a, **k: next(results), proc_root=tmp)
            self.assertTrue(sample['complete'])
            self.assertEqual([p['gpu_index'] for p in sample['processes']], [0, 0])
            self.assertEqual([p['username'] for p in sample['processes']], ['wkli', 'wkli'])

    def test_query_or_disappearing_process_is_unknown_not_idle(self):
        def failure(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 12)
        self.assertFalse(process_sampling.sample_gpu_processes(run=failure)['complete'])
        with tempfile.TemporaryDirectory() as tmp:
            results = iter([SimpleNamespace(stdout='0, GPU-a\n'), SimpleNamespace(stdout='GPU-a, 123, 200\n')])
            sample = process_sampling.sample_gpu_processes(run=lambda *a, **k: next(results), proc_root=tmp)
            self.assertFalse(sample['complete'])
            self.assertEqual(sample['unresolved_count'], 1)

if __name__ == '__main__':
    unittest.main()
