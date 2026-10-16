"""Check frequency/runtime intersections using the official processor."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.cpu_work import cpu_work_rows


class CpuWorkTests(unittest.TestCase):
    def test_partial_frequency_and_cpu_migration(self) -> None:
        trace = """# tracer: nop
 worker-10 (   10) [000] ...1     1.000000: tracing_mark_write: B|10|trace
 swapper-0 (-----) [000] ...1     1.000000: sched_switch: prev_comm=swapper prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=worker next_pid=10 next_prio=120
 worker-10 (   10) [000] ...1     1.002000: cpu_frequency: state=1000000 cpu_id=0
 worker-10 (   10) [000] ...1     1.004000: cpu_frequency: state=2000000 cpu_id=0
 worker-10 (   10) [000] ...1     1.006000: sched_switch: prev_comm=worker prev_pid=10 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120
 swapper-0 (-----) [001] ...1     1.006000: sched_switch: prev_comm=swapper prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=worker next_pid=10 next_prio=120
 worker-10 (   10) [001] ...1     1.008000: sched_switch: prev_comm=worker prev_pid=10 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120
 swapper-0 (-----) [002] ...1     1.008000: sched_switch: prev_comm=swapper prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=unknown next_pid=30 next_prio=120
 unknown-30 (-----) [002] ...1     1.009000: sched_switch: prev_comm=unknown prev_pid=30 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120
 worker-10 (   10) [000] ...1     1.010000: tracing_mark_write: E
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "work.systrace"
            path.write_text(trace)
            with TraceProcessor(trace=str(path)) as tp:
                rows = cpu_work_rows(tp, pid=10)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertAlmostEqual(row.runtime_ns, 8000000, delta=2)
                self.assertAlmostEqual(row.covered_ns, 4000000, delta=2)
                self.assertAlmostEqual(row.coverage_pct, 50, delta=0.001)
                self.assertAlmostEqual(row.avg_freq_khz, 1500000, delta=1)
                self.assertAlmostEqual(row.megacycles, 6, delta=0.001)
                threads = cpu_work_rows(tp, threads=True, pid=10)
                self.assertEqual(len(threads), 1)
                self.assertEqual(threads[0].megacycles, row.megacycles)
                self.assertEqual(cpu_work_rows(tp, pid=999), [])
                unknown = list(tp.query("SELECT upid FROM thread WHERE tid=30"))[0]
                self.assertIsNone(unknown.upid)
                orphan = [
                    r for r in cpu_work_rows(tp, threads=True) if r.entity_id == 30
                ]
                self.assertEqual(len(orphan), 1)
                self.assertAlmostEqual(orphan[0].runtime_ns, 1000000, delta=2)
                self.assertIsNone(orphan[0].avg_freq_khz)
                self.assertEqual(
                    list(
                        tp.query(
                            "SELECT name FROM sqlite_master WHERE name GLOB 'summary_work_*'"
                        )
                    ),
                    [],
                )
            path.write_text(
                "\n".join(
                    line for line in trace.splitlines() if "cpu_frequency:" not in line
                )
            )
            with TraceProcessor(trace=str(path)) as tp:
                row = cpu_work_rows(tp, pid=10)[0]
                self.assertEqual(row.covered_ns, 0)
                self.assertEqual(row.coverage_pct, 0)
                self.assertIsNone(row.avg_freq_khz)
                self.assertIsNone(row.megacycles)

    def test_without_sched_is_empty(self) -> None:
        with TraceProcessor(
            trace=str(Path(__file__).parent / "fixtures/metrics.systrace")
        ) as tp:
            self.assertEqual(cpu_work_rows(tp), [])
