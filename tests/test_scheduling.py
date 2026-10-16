"""Exercise runnable-state selection, identity and percentile definitions."""

import contextlib
import io
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.scheduling import scheduling_rows, show_scheduling_wait


class SchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE thread_state(id INTEGER PRIMARY KEY, utid INTEGER, state TEXT, dur INTEGER);
            CREATE TABLE thread(utid INTEGER, upid INTEGER, tid INTEGER, name TEXT, is_idle INTEGER);
            CREATE TABLE process(upid INTEGER, pid INTEGER, name TEXT);
            INSERT INTO process VALUES(1,10,'first'),(2,10,'reused');
            INSERT INTO thread VALUES(1,1,10,'main',0),(2,1,11,'worker',0),(3,2,10,'reused',0),(4,NULL,30,'unknown',0),(0,0,0,'idle',1);
        """)

    def query(self, sql: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(**dict(row)) for row in self.db.execute(sql)]

    def test_nearest_rank_and_state_filtering(self) -> None:
        self.db.executemany(
            "INSERT INTO thread_state(utid,state,dur) VALUES(1,?,?)",
            [("R+" if i % 2 else "R", i) for i in range(1, 21)]
            + [("R", 0), ("R", -1), ("S", 1000), ("D", 1000)],
        )
        self.db.execute("INSERT INTO thread_state(utid,state,dur) VALUES(0,'R',1000)")
        row = scheduling_rows(self)[0]
        self.assertEqual((row.wait_ns, row.wait_count), (210, 20))
        self.assertEqual((row.p95_wait_ns, row.max_wait_ns), (19, 20))

    def test_process_aggregation_pid_reuse_and_orphan_thread(self) -> None:
        self.db.executescript("""
            INSERT INTO thread_state VALUES(1,1,'R',5),(2,2,'R+',10),(3,3,'R',20),(4,4,'R',7);
        """)
        rows = scheduling_rows(self, pid=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.entity_key for r in rows], [2, 1])
        self.assertEqual(rows[1].wait_ns, 15)
        self.assertEqual(rows[1].p95_wait_ns, 10)
        threads = scheduling_rows(self, threads=True)
        self.assertIn(30, [r.entity_id for r in threads])
        self.assertNotIn(
            30, [r.entity_id for r in scheduling_rows(self, threads=True, pid=10)]
        )
        self.assertEqual(scheduling_rows(self, pid=999), [])

    def test_empty_data_is_unavailable(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_scheduling_wait(self)
        self.assertEqual(output.getvalue().count("N/A:"), 2)

    def test_real_processor_runnable_and_preempted_intervals(self) -> None:
        trace = """# tracer: nop
 worker-10 (   10) [000] ...1     1.000000: tracing_mark_write: B|10|trace
 swapper-0 (-----) [000] ...1     1.000000: sched_switch: prev_comm=swapper prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=other next_pid=20 next_prio=120
 other-20 (   20) [000] ...1     1.001000: sched_waking: comm=worker pid=10 prio=120 target_cpu=0
 other-20 (   20) [000] ...1     1.002000: sched_switch: prev_comm=other prev_pid=20 prev_prio=120 prev_state=S ==> next_comm=worker next_pid=10 next_prio=120
 worker-10 (   10) [000] ...1     1.004000: sched_switch: prev_comm=worker prev_pid=10 prev_prio=120 prev_state=R+ ==> next_comm=other next_pid=20 next_prio=120
 other-20 (   20) [000] ...1     1.006000: sched_switch: prev_comm=other prev_pid=20 prev_prio=120 prev_state=S ==> next_comm=worker next_pid=10 next_prio=120
 worker-10 (   10) [000] ...1     1.008000: sched_switch: prev_comm=worker prev_pid=10 prev_prio=120 prev_state=S ==> next_comm=swapper next_pid=0 next_prio=120
 worker-10 (   10) [000] ...1     1.010000: tracing_mark_write: E
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scheduling.systrace"
            path.write_text(trace)
            with TraceProcessor(trace=str(path)) as tp:
                row = scheduling_rows(tp, pid=10)[0]
                self.assertEqual(row.wait_count, 2)
                self.assertAlmostEqual(row.wait_ns, 3000000, delta=2)
                self.assertAlmostEqual(row.max_wait_ns, 2000000, delta=2)
                self.assertEqual(row.p95_wait_ns, row.max_wait_ns)
