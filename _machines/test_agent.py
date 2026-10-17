import importlib.util
import pathlib
import unittest
import tempfile
from unittest.mock import patch
import subprocess

spec = importlib.util.spec_from_file_location('agent', pathlib.Path(__file__).with_name('agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class ParsingTests(unittest.TestCase):
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
