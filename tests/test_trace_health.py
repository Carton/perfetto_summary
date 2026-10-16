"""Exercise health diagnostics with the actual official processor."""

import contextlib
import io
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.trace_health import coverage_rows, show_trace_health


class TraceHealthTests(unittest.TestCase):
    def test_identity_diagnostics_and_machine_local_coverage(self) -> None:
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.create_function("trace_start", 0, lambda: 0)
        db.create_function("trace_end", 0, lambda: 1000)
        db.executescript("""
            CREATE TABLE cpu(id,cpu,machine_id);
            INSERT INTO cpu VALUES(0,0,NULL),(1,0,1),(2,2,NULL);
            CREATE TABLE cpu_frequency_counters(ucpu,dur,freq);
            INSERT INTO cpu_frequency_counters VALUES(0,800,1000),(0,400,2000),(1,500,1000);
            CREATE TABLE cpu_idle_counters(track_id,cpu,dur,idle);
            INSERT INTO cpu_idle_counters VALUES(10,0,700,-1),(11,0,300,0);
            CREATE TABLE cpu_counter_track(id,machine_id);
            INSERT INTO cpu_counter_track VALUES(10,NULL),(11,1);
            CREATE TABLE stats(name,idx,severity,value);
            INSERT INTO stats VALUES('lost_packets',0,'data_loss',2),('zero_error',0,'error',0),('info_only',0,'info',9);
            CREATE TABLE process(upid,pid,name);
            INSERT INTO process VALUES(1,10,NULL),(2,4294901760,'bad'),(3,0,NULL);
            CREATE TABLE thread(utid,upid,is_idle);
            INSERT INTO thread VALUES(1,1,0),(2,2,0),(3,3,1);
            CREATE TABLE sched(utid,dur);
            INSERT INTO sched VALUES(1,5),(2,5),(3,5);
            CREATE TABLE thread_state(id);
            CREATE TABLE slice(id);
            CREATE TABLE actual_frame_timeline_slice(id);
        """)

        class Processor:
            http = SimpleNamespace(
                status=lambda: SimpleNamespace(
                    human_readable_version="test", api_version=14
                )
            )

            def query(self, sql):
                if sql.startswith("INCLUDE"):
                    return []
                return [SimpleNamespace(**dict(r)) for r in db.execute(sql)]

        tp = Processor()
        rows = {(r.ucpu, r.signal): r for r in coverage_rows(tp)}
        self.assertEqual(rows[0, "Idle"].coverage_pct, 70)
        self.assertEqual(rows[1, "Idle"].coverage_pct, 30)
        self.assertIsNone(rows[2, "Idle"].coverage_pct)
        self.assertEqual(rows[0, "Frequency"].coverage_pct, 120)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_trace_health(tp)
        text = output.getvalue()
        self.assertIn("lost_packets", text)
        self.assertNotIn("zero_error", text)
        self.assertNotIn("info_only", text)
        self.assertIn("identity warnings: 2", text)
        self.assertIn("PID outside Linux range", text)
        self.assertIn("missing process name", text)

    def test_partial_frequency_and_missing_idle(self) -> None:
        with TraceProcessor(
            trace=str(Path(__file__).parent / "fixtures/metrics.systrace")
        ) as tp:
            rows = coverage_rows(tp)
            frequency = {r.cpu: r for r in rows if r.signal == "Frequency"}
            self.assertAlmostEqual(frequency[0].coverage_pct, 80)
            self.assertAlmostEqual(frequency[3].coverage_pct, 70, delta=0.00002)
            self.assertTrue(all(r.known_ns is None for r in rows if r.signal == "Idle"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                show_trace_health(tp)
            text = output.getvalue()
            self.assertIn("Processor: Perfetto", text)
            self.assertIn("no records", text)
            self.assertIn("N/A", text)
            self.assertIn("No positive error/data_loss", text)
