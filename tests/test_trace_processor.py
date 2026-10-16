"""Exercise metric SQL against the official processor with a synthetic trace."""

import contextlib
import io
import unittest
from pathlib import Path

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.cli import PerfettoParser


class TraceProcessorMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tp = TraceProcessor(
            trace=str(Path(__file__).parent / "fixtures/metrics.systrace")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tp.close()

    def setUp(self) -> None:
        self.parser = PerfettoParser()
        self.parser._tp = self.tp
        self.output = io.StringIO()
        self.assertTrue(self.parser.parse_basic_info())

    def frequency_rows(self, cpu: int):
        with contextlib.redirect_stdout(self.output):
            self.parser.do_cpu_freq_stats()
        return list(
            self.tp.query(
                f"SELECT freq, sum(dur) AS dur FROM cpu_frequency_counters WHERE cpu={cpu} AND dur>0 GROUP BY freq ORDER BY freq"
            )
        )

    def test_trace_bounds_work_without_sched(self) -> None:
        self.assertEqual(list(self.tp.query("SELECT count(*) AS n FROM sched"))[0].n, 0)
        self.assertEqual(self.parser.get_start_ts(), 1000000000)
        self.assertEqual(self.parser.get_trace_duration(), 10000000)

    def test_frequency_last_sample_and_repeated_value(self) -> None:
        rows = self.frequency_rows(0)
        self.assertEqual([row.freq for row in rows], [1000000, 2000000])
        self.assertAlmostEqual(rows[0].dur, 3000000, delta=2)
        self.assertAlmostEqual(rows[1].dur, 5000000, delta=2)
        self.assertEqual(sum(row.dur for row in rows), 8000000)
        self.assertIn("50.0%", self.output.getvalue())

    def test_frequency_single_sample_covers_tail_only(self) -> None:
        rows = self.frequency_rows(3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].freq, 500000)
        self.assertAlmostEqual(rows[0].dur, 7000000, delta=2)
        self.assertIn("CPU 3:", self.output.getvalue())
        self.assertIn("CPU 9:", self.output.getvalue())

    def test_equal_timestamp_frequency_has_no_double_duration(self) -> None:
        rows = self.frequency_rows(9)
        self.assertEqual([row.freq for row in rows], [1000000, 1500000])
        self.assertAlmostEqual(rows[0].dur, 1000000, delta=2)
        self.assertAlmostEqual(rows[1].dur, 5000000, delta=2)
        self.assertEqual(sum(row.dur for row in rows), 6000000)
        self.assertEqual(self.output.getvalue().count("1200000"), 0)
        self.assertRegex(self.output.getvalue(), r"1500000\s+5\.000\s+1\s+50\.0%")

    def test_fps_nested_wrappers_in_real_parser(self) -> None:
        self.parser.query_proc_names()
        with contextlib.redirect_stdout(self.output):
            threads = self.parser.do_fps_stat()
        self.assertEqual(len(threads), 1)
        self.assertEqual(
            list(self.tp.query("SELECT count(*) AS n FROM frame_slices"))[0].n, 2
        )
        self.assertEqual(
            list(self.tp.query("SELECT count(*) AS n FROM thread_durs"))[0].n, 1
        )
        self.assertIn("200.000", self.output.getvalue())

    def test_overlapping_gpu_waits_remain_individual_samples(self) -> None:
        self.parser.query_proc_names()
        with contextlib.redirect_stdout(self.output):
            self.parser.do_gpu_complete_stat()
        # Two different processes wait concurrently for 5ms each, not one 6ms wait.
        rows = list(
            self.tp.query(
                "SELECT dur FROM slice WHERE name = 'waiting for GPU completion' ORDER BY ts"
            )
        )
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertAlmostEqual(row.dur, 5000000, delta=2)
        self.assertEqual(self.output.getvalue().count("04~09ms"), 2)
