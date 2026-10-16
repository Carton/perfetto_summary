"""Validate idle transitions and residence against the real parser."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.cpu_idle import cpu_idle_rows


class CpuIdleTests(unittest.TestCase):
    def test_residency_unknown_prefix_tail_and_duplicate_timestamp(self) -> None:
        trace = """# tracer: nop
 task-10 (   10) [000] ...1     1.000000: tracing_mark_write: B|10|window
 task-10 (   10) [000] ...1     1.002000: cpu_idle: state=0 cpu_id=0
 task-10 (   10) [000] ...1     1.004000: cpu_idle: state=1 cpu_id=0
 task-10 (   10) [000] ...1     1.005000: cpu_idle: state=4294967295 cpu_id=0
 task-10 (   10) [000] ...1     1.006000: cpu_idle: state=1 cpu_id=0
 task-10 (   10) [000] ...1     1.006000: cpu_idle: state=4294967295 cpu_id=0
 task-10 (   10) [001] ...1     1.007000: cpu_idle: state=0 cpu_id=1
 task-10 (   10) [001] ...1     1.010000: cpu_idle: state=4294967295 cpu_id=1
 task-10 (   10) [000] ...1     1.010000: tracing_mark_write: E
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "idle.systrace"
            path.write_text(trace)
            with TraceProcessor(trace=str(path)) as tp:
                rows = cpu_idle_rows(tp)
                cpu0 = {r.idle: r for r in rows if r.cpu == 0}
                self.assertAlmostEqual(cpu0[0].duration_ns, 2e6, delta=2)
                self.assertAlmostEqual(cpu0[1].duration_ns, 1e6, delta=2)
                self.assertAlmostEqual(cpu0[-1].duration_ns, 5e6, delta=2)
                self.assertEqual(cpu0[-1].exits, 2)
                self.assertAlmostEqual(cpu0[0].coverage_pct, 80, places=3)
                cpu1 = [r for r in rows if r.cpu == 1]
                self.assertEqual(len(cpu1), 2)
                self.assertAlmostEqual(sum(r.duration_ns for r in cpu1), 3e6, delta=2)
                self.assertEqual(sum(r.exits for r in cpu1), 1)
                self.assertEqual(cpu1[0].duration_ns, 0)
                self.assertEqual(cpu_idle_rows(tp)[0].duration_ns, rows[0].duration_ns)

    def test_missing_idle_is_unavailable(self) -> None:
        with TraceProcessor(
            trace=str(Path(__file__).parent / "fixtures/metrics.systrace")
        ) as tp:
            self.assertEqual(cpu_idle_rows(tp), [])
