import contextlib
import io
import re
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from perfetto_summary import cli
from perfetto_summary.cli import PerfettoParser, ThreadInfo, durations_to_fpses


class SqlProcessor:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE process(upid INTEGER, pid INTEGER);
            CREATE TABLE thread(utid INTEGER, upid INTEGER, tid INTEGER, name TEXT);
            CREATE TABLE thread_track(id INTEGER, utid INTEGER);
            CREATE TABLE slice(id INTEGER PRIMARY KEY, track_id INTEGER, ts INTEGER, name TEXT, parent_id INTEGER, dur INTEGER DEFAULT 0);
            CREATE TABLE process_counter_track(id INTEGER, name TEXT, upid INTEGER);
            CREATE TABLE counter(id INTEGER PRIMARY KEY, track_id INTEGER, ts INTEGER, value INTEGER);
            CREATE TABLE thread_state(utid INTEGER, ts INTEGER, dur INTEGER, state TEXT, io_wait INTEGER);
            INSERT INTO process VALUES(1, 10);
            INSERT INTO thread VALUES(1, 1, 10, 'RenderThread');
            INSERT INTO thread_track VALUES(1, 1);
        """)

    def query(self, sql):
        return [SimpleNamespace(**dict(row)) for row in self.db.execute(sql)]


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.parser = PerfettoParser("unused")
        self.tp = SqlProcessor()
        self.parser._tp = self.tp
        self.parser._procnames = {1: "app"}
        self.parser._procname_len = 12
        self.output = io.StringIO()
        self.addCleanup(self.tp.db.close)

    def test_fps_zero_span_and_surfaceflinger_suffix(self):
        selected = []
        self.parser.do_render_threads_stat = selected.extend
        self.parser.do_surfaceview_stat = lambda threads: None
        self.tp.db.executescript("""
            INSERT INTO slice(track_id, ts, name) VALUES(1, 0, 'eglSwapBuffers');
            INSERT INTO slice(track_id, ts, name) VALUES(1, 0, 'eglSwapBuffers');
        """)
        with contextlib.redirect_stdout(self.output):
            self.parser.do_fps_stat()
        self.assertEqual(selected, [])
        self.tp.db.executescript("""
            DELETE FROM slice;
            INSERT INTO slice(track_id, ts, name) VALUES(1, 0, 'postFramebuffer display 0');
            INSERT INTO slice(track_id, ts, name) VALUES(1, 16000000, 'postFramebuffer display 0');
        """)
        with contextlib.redirect_stdout(self.output):
            threads = self.parser.do_fps_stat()
        self.assertEqual(len(threads), 1)
        self.assertEqual(selected, [])
        self.assertIn("62.500", self.output.getvalue())

    def test_surfaceview_sparse_and_positive_events(self):
        self.tp.db.execute(
            "INSERT INTO process_counter_track VALUES(1, 'BufferTX - SurfaceView[test]', 1)"
        )
        for samples, expected in [
            ([], "FPS 0.000, Count 0"),
            ([(0, 0), (10000000, 1)], "FPS 0.000, Count 0"),
            (
                [(0, 0), (10000000, 1), (20000000, 0), (30000000, 1), (40000000, 4)],
                "FPS 66.667, Count 2",
            ),
        ]:
            with self.subTest(samples=samples):
                self.tp.db.execute("DELETE FROM counter")
                self.tp.db.executemany(
                    "INSERT INTO counter(track_id, ts, value) VALUES(1, ?, ?)", samples
                )
                self.output = io.StringIO()
                with contextlib.redirect_stdout(self.output):
                    self.parser.do_surfaceview_stat([])
                self.assertIn(expected, self.output.getvalue())

    def test_missing_process_names_and_schedule_samples(self):
        self.tp.db.execute("ALTER TABLE thread ADD COLUMN is_main_thread INTEGER")
        self.tp.db.execute("ALTER TABLE process ADD COLUMN name TEXT")
        self.tp.db.execute("DELETE FROM thread")
        self.parser.query_proc_names()
        self.assertEqual(self.parser.get_proc_name(99), "<unknown>")
        with contextlib.redirect_stdout(self.output):
            self.parser.do_schedule_stat([ThreadInfo("render", 1, 1, 10, 10)])

    def test_process_names_are_not_truncated_for_display(self):
        name = "[red]com.example." + "long_process_name_" * 10
        self.tp.db.execute("ALTER TABLE process ADD COLUMN name TEXT")
        self.tp.db.execute("UPDATE process SET name=?", (name,))
        self.parser.query_proc_names()
        self.assertEqual(self.parser.get_proc_name(1), name)

    def test_vsync_nanosecond_cutoff(self):
        self.assertEqual(durations_to_fpses([0, 1, 2500000, 5000000]), [200.0])

    def run_fps(self):
        self.parser.do_render_threads_stat = lambda threads: None
        self.parser.do_surfaceview_stat = lambda threads: None
        with contextlib.redirect_stdout(self.output):
            self.parser.do_fps_stat()

    def test_fps_uses_n_minus_one_intervals(self):
        self.tp.db.executemany(
            "INSERT INTO slice(track_id, ts, name) VALUES(1, ?, 'eglSwapBuffers')",
            [(0,), (16000000,), (36000000,)],
        )
        self.run_fps()
        self.assertIn("55.556", self.output.getvalue())

    def test_empty_and_single_submission_have_no_interval(self):
        for count in [0, 1]:
            with self.subTest(count=count):
                self.tp.db.execute("DELETE FROM slice")
                if count:
                    self.tp.db.execute(
                        "INSERT INTO slice(track_id, ts, name) VALUES(1, 0, 'eglSwapBuffers')"
                    )
                self.run_fps()
                self.assertEqual(
                    len(self.tp.query("SELECT * FROM frame_slices")), count
                )

    def test_root_slices_are_not_merged_by_time_overlap(self):
        self.tp.db.executescript("""
            INSERT INTO slice(track_id, ts, name, dur) VALUES
              (1, 0, 'eglSwapBuffers', 10000000),
              (1, 5000000, 'eglSwapBuffers', 10000000);
        """)
        self.run_fps()
        self.assertEqual(len(self.tp.query("SELECT * FROM frame_slices")), 2)
        self.assertIn("200.000", self.output.getvalue())

    def test_frame_histogram_inclusive_upper_boundaries(self):
        timestamps = [0]
        for interval in [0, 4000000, 9000000, 12000000, 16000000, 18000000, 18000001]:
            timestamps.append(timestamps[-1] + interval)
        self.tp.db.executemany(
            "INSERT INTO slice(track_id, ts, name) VALUES(1, ?, 'eglSwapBuffers')",
            [(ts,) for ts in timestamps],
        )
        self.run_fps()
        with contextlib.redirect_stdout(self.output):
            PerfettoParser.do_render_threads_stat(
                self.parser, [ThreadInfo("render", 1, 1, 10, 10)]
            )
        buckets = dict(
            re.findall(
                r"(00~04ms|04~09ms|09~12ms|12~16ms|16~18ms|18\+ms)\s+(\d+)",
                self.output.getvalue(),
            )
        )
        self.assertEqual(
            buckets,
            {
                "00~04ms": "2",
                "04~09ms": "1",
                "09~12ms": "1",
                "12~16ms": "1",
                "16~18ms": "1",
                "18+ms": "1",
            },
        )

    def test_nested_egl_wrappers_count_as_one_submission(self):
        self.tp.db.executescript("""
            INSERT INTO slice(id, track_id, ts, name, parent_id, dur) VALUES
              (1, 1, 0, 'eglSwapBuffers', NULL, 10000000),
              (2, 1, 100000, 'wrapper', 1, 5000000),
              (3, 1, 200000, 'eglSwapBuffersWithDamageKHR', 2, 2000000),
              (4, 1, 20000000, 'eglSwapBuffers', NULL, 1000000);
        """)
        self.run_fps()
        self.assertEqual(
            [r.id for r in self.tp.query("SELECT id FROM frame_slices ORDER BY id")],
            [1, 4],
        )
        self.assertIn("50.000", self.output.getvalue())

    def test_parallel_frame_tracks_do_not_mix_intervals(self):
        self.tp.db.executescript("""
            INSERT INTO thread_track VALUES(2, 1);
            INSERT INTO slice(track_id, ts, name) VALUES
              (1, 0, 'eglSwapBuffers'), (1, 20000000, 'eglSwapBuffers'),
              (2, 1000000, 'eglSwapBuffers'), (2, 21000000, 'eglSwapBuffers');
        """)
        self.run_fps()
        with contextlib.redirect_stdout(self.output):
            PerfettoParser.do_render_threads_stat(
                self.parser, [ThreadInfo("render", 1, 1, 10, 10)]
            )
        self.assertEqual(
            [r.dur for r in self.tp.query("SELECT dur FROM thread_durs")],
            [20000000, 20000000],
        )

    def test_surfaceview_same_timestamp_uses_counter_id(self):
        self.tp.db.execute(
            "INSERT INTO process_counter_track VALUES(1, 'BufferTX - SurfaceView[test]', 1)"
        )
        self.tp.db.executescript("""
            INSERT INTO counter(id, track_id, ts, value) VALUES
              (1, 1, 0, 0), (3, 1, 10000000, 0),
              (2, 1, 10000000, 1), (4, 1, 20000000, 1);
        """)
        with contextlib.redirect_stdout(self.output):
            self.parser.do_surfaceview_stat([])
        self.assertIn("FPS 100.000, Count 1", self.output.getvalue())

    def test_vsync_tracks_have_independent_predecessors(self):
        self.tp.db.executescript("""
            INSERT INTO process_counter_track VALUES(1, 'VSYNC-app', 1), (2, 'VSYNC-app', 1);
            INSERT INTO counter(track_id, ts, value) VALUES
              (1, 0, 0), (1, 20000000, 1), (2, 1000000, 0), (2, 21000000, 1);
        """)
        with contextlib.redirect_stdout(self.output):
            self.parser.do_vsync_stat_on("VSYNC-app")
        self.assertIn("avg Hz: 50.00", self.output.getvalue())

    def test_dynamic_cpu_ids_do_not_index_an_eight_cpu_array(self):
        self.tp.db.executescript("""
            CREATE TABLE sched(utid INTEGER, cpu INTEGER, dur INTEGER);
            INSERT INTO sched VALUES(1, 0, 1000000), (1, 9, 2000000);
        """)
        self.parser._trace_dur_ns = 10000000
        with (
            patch.object(
                cli,
                "args",
                SimpleNamespace(cpu_type="sm8850", procs_num=10),
                create=True,
            ),
            contextlib.redirect_stdout(self.output),
        ):
            self.parser.do_cpu_time_per_process(10)
        self.assertIn("CPU9=66%", self.output.getvalue())
        self.assertIn("CPU0=33%", self.output.getvalue())
        self.assertNotIn("CPU7=", self.output.getvalue())
        self.assertIn("N/A", self.output.getvalue())

    def test_normalized_capacity_in_process_output(self):
        self.tp.db.executescript("""
            CREATE TABLE sched(utid INTEGER, cpu INTEGER, dur INTEGER);
            INSERT INTO sched VALUES(1, 0, 10000000), (1, 7, 10000000);
        """)
        self.parser._trace_dur_ns = 10000000
        with (
            patch.object(
                cli,
                "args",
                SimpleNamespace(cpu_type="mt6899", procs_num=1),
                create=True,
            ),
            contextlib.redirect_stdout(self.output),
        ):
            self.parser.do_cpu_time_per_process(10)
        # Two fully busy cores contribute (697 + 1024) / 6599 of the device.
        self.assertIn("26.080", self.output.getvalue())
        self.assertIn("200.0%", self.output.getvalue())

    def test_nonpositive_trace_bounds_are_rejected(self):
        for start, end in [(0, 0), (20, 10), (None, None)]:
            with self.subTest(start=start, end=end):
                self.tp.db.create_function("trace_start", 0, lambda: start)
                self.tp.db.create_function("trace_end", 0, lambda: end)
                with contextlib.redirect_stdout(self.output):
                    self.assertFalse(self.parser.parse_basic_info())


if __name__ == "__main__":
    unittest.main()
