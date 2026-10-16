import contextlib
import io
import sqlite3
import unittest
from types import SimpleNamespace

from perfetto_summary.cli import PerfettoParser, ThreadInfo, durations_to_fpses


class SqlProcessor:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE process(upid INTEGER, pid INTEGER);
            CREATE TABLE thread(utid INTEGER, upid INTEGER, tid INTEGER, name TEXT);
            CREATE TABLE thread_track(id INTEGER, utid INTEGER);
            CREATE TABLE slice(track_id INTEGER, ts INTEGER, name TEXT);
            CREATE TABLE process_counter_track(id INTEGER, name TEXT, upid INTEGER);
            CREATE TABLE counter(track_id INTEGER, ts INTEGER, value INTEGER);
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
            INSERT INTO slice VALUES(1, 0, 'eglSwapBuffers');
            INSERT INTO slice VALUES(1, 0, 'eglSwapBuffers');
        """)
        with contextlib.redirect_stdout(self.output):
            self.parser.do_fps_stat()
        self.assertEqual(selected, [])
        self.tp.db.executescript("""
            DELETE FROM slice;
            INSERT INTO slice VALUES(1, 0, 'postFramebuffer display 0');
            INSERT INTO slice VALUES(1, 16000000, 'postFramebuffer display 0');
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
                self.tp.db.executemany("INSERT INTO counter VALUES(1, ?, ?)", samples)
                self.output = io.StringIO()
                with contextlib.redirect_stdout(self.output):
                    self.parser.do_surfaceview_stat([])
                self.assertIn(expected, self.output.getvalue())

    def test_missing_process_names_and_schedule_samples(self):
        self.tp.db.execute(
            "CREATE TABLE thread_state(utid INTEGER, dur INTEGER, state TEXT, io_wait INTEGER)"
        )
        self.tp.db.execute("ALTER TABLE thread ADD COLUMN is_main_thread INTEGER")
        self.tp.db.execute("ALTER TABLE process ADD COLUMN name TEXT")
        self.tp.db.execute("DELETE FROM thread")
        self.parser.query_proc_names()
        self.assertEqual(self.parser.get_proc_name(99), "<unknown>")
        with contextlib.redirect_stdout(self.output):
            self.parser.do_schedule_stat([ThreadInfo("render", 1, 1, 10, 10)])

    def test_vsync_nanosecond_cutoff(self):
        self.assertEqual(durations_to_fpses([0, 1, 2500000, 5000000]), [200.0])


if __name__ == "__main__":
    unittest.main()
