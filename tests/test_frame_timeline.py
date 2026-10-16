"""Exercise timeline identities, incomplete records and outcome labels."""

import contextlib
import io
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from perfetto.trace_processor import TraceProcessor

from perfetto_summary.frame_timeline import frame_timeline_rows, show_frame_timeline


class SqlProcessor:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE process(upid INTEGER, pid INTEGER, name TEXT);
            CREATE TABLE actual_frame_timeline_slice(
                id INTEGER, upid INTEGER, layer_name TEXT, surface_frame_token INTEGER,
                display_frame_token INTEGER, dur INTEGER, jank_tag TEXT,
                jank_type TEXT, present_type TEXT);
            INSERT INTO process VALUES(1, 10, 'example'), (2, 10, 'reused-pid');
        """)

    def query(self, sql):
        return [SimpleNamespace(**dict(r)) for r in self.db.execute(sql)]


class FrameTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tp = SqlProcessor()
        self.addCleanup(self.tp.db.close)

    def add(
        self,
        ident,
        upid=1,
        layer="layer-a",
        token=1,
        duration=10,
        tag="No Jank",
        cause="None",
        present="On-time Present",
    ) -> None:
        self.tp.db.execute(
            "INSERT INTO actual_frame_timeline_slice VALUES(?,?,?,?,?,?,?,?,?)",
            (ident, upid, layer, token, 100, duration, tag, cause, present),
        )

    def test_layer_token_dedup_and_complete_precedence(self) -> None:
        self.add(1)
        self.add(2, duration=-1)
        self.add(3, layer="layer-b")
        self.add(4, upid=2)
        rows = frame_timeline_rows(self.tp, pid=10)
        self.assertEqual(sum(r.complete for r in rows), 3)
        self.assertEqual(sum(r.duplicates for r in rows), 1)
        self.assertEqual(sum(r.incomplete for r in rows), 1)
        self.assertEqual({r.upid for r in rows}, {1, 2})
        self.assertEqual(frame_timeline_rows(self.tp, pid=99), [])

    def test_invalid_tokens_and_sf_records_remain_separate(self) -> None:
        for ident in range(1, 4):
            self.add(ident, token=None)
        self.add(4, token=0, layer=None)
        self.add(5, token=0, layer=None)
        self.add(6, upid=None, token=5)
        self.add(7, upid=None, token=5)
        self.add(8, token=None, layer=None)
        rows = frame_timeline_rows(self.tp)
        self.assertEqual(sum(r.complete for r in rows), 8)
        self.assertEqual(sum(r.complete for r in rows if r.kind == "SF"), 3)
        self.assertEqual(sum(r.unkeyed for r in rows), 5)
        self.assertEqual(sum(r.duplicates for r in rows), 0)

    def test_stuffing_unknown_and_dropped_outcomes(self) -> None:
        self.add(
            1, tag="Buffer Stuffing", cause="Buffer Stuffing", present="Late Present"
        )
        self.add(
            2,
            token=2,
            tag="Self Jank",
            cause="App Deadline Missed, Buffer Stuffing",
            present="Late Present",
        )
        self.add(3, token=3, tag=None, cause=None, present=None)
        self.add(
            4,
            token=4,
            tag="Dropped Frame",
            cause="Dropped Frame",
            present="Dropped Frame",
        )
        self.add(5, token=5, duration=-1)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_frame_timeline(self.tp)
        text = output.getvalue()
        self.assertIn(
            "Complete=4 Tagged jank=1/3 (33.33%) Late=2 Dropped=1 Unknown tag=1", text
        )
        self.assertIn("Incomplete records=1", text)
        self.assertIn("App Deadline Missed, Buffer Stuffing", text)

    def test_missing_timeline_with_official_processor(self) -> None:
        with TraceProcessor(
            trace=str(Path(__file__).parent / "fixtures/metrics.systrace")
        ) as tp:
            self.assertEqual(frame_timeline_rows(tp), [])

    def test_known_non_jank_tags_and_empty_layer_identity(self) -> None:
        self.add(1, tag="SurfaceFlinger Stuffing")
        self.add(2, token=2, tag="Non-perceivable Jank")
        self.add(3, token=3, tag="Self Jank")
        self.add(4, token=4, tag="Unspecified", layer="")
        self.add(5, token=4, tag="Unspecified", layer="")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_frame_timeline(self.tp)
        self.assertIn("Tagged jank=1/3 (33.33%)", output.getvalue())
        self.assertIn("Complete=2 Tagged jank=0/0 (N/A)", output.getvalue())
        self.assertEqual(sum(r.unkeyed for r in frame_timeline_rows(self.tp)), 2)
