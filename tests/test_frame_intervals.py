"""Validate interval ranks and half-open thread-state window partitioning."""

import sqlite3
import unittest
from types import SimpleNamespace

from perfetto_summary.frame_intervals import interval_percentiles, state_breakdown


class FrameIntervalTests(unittest.TestCase):
    def test_missing_state_is_unknown(self) -> None:
        rows = [
            SimpleNamespace(ts=0, dur=5, state=None),
            SimpleNamespace(ts=5, dur=5, state=""),
        ]
        self.assertEqual(state_breakdown(rows, 0, 10)["Unknown"], 10)

    def test_nearest_rank_zero_and_empty(self) -> None:
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE thread_durs(dur INTEGER)")

        class Processor:
            def query(self, sql):
                return [SimpleNamespace(**dict(r)) for r in db.execute(sql)]

        tp = Processor()
        self.assertIsNone(interval_percentiles(tp).p95_ns)
        db.executemany(
            "INSERT INTO thread_durs VALUES(?)",
            [(i,) for i in range(100)] + [(-1,), (None,)],
        )
        row = interval_percentiles(tp)
        self.assertEqual((row.samples, row.p95_ns, row.p99_ns), (100, 94, 98))
        db.execute("CREATE TABLE sv_durs(dur INTEGER)")
        db.execute("INSERT INTO sv_durs VALUES(123)")
        surface = interval_percentiles(tp, surfaceview=True)
        self.assertEqual(
            (surface.samples, surface.p95_ns, surface.p99_ns), (1, 123, 123)
        )

    def test_clipping_unknown_unfinished_and_end_boundary(self) -> None:
        rows = [
            SimpleNamespace(ts=ts, dur=dur, state=state)
            for ts, dur, state in [
                (0, 15, "Running"),
                (15, 5, "R"),
                (20, 5, "R+"),
                (30, 5, "D|K"),
                (35, -1, "S"),
                (40, 10, "Running"),
            ]
        ]
        result = state_breakdown(rows, 10, 40)
        self.assertEqual(
            result,
            {
                "Running": 5,
                "Runnable": 10,
                "Sleeping": 5,
                "Blocked": 5,
                "Other": 0,
                "Unknown": 5,
                "Overlap": 0,
            },
        )
        self.assertEqual(sum(result.values()), 30)

    def test_overlap_is_not_double_counted(self) -> None:
        rows = [
            SimpleNamespace(ts=0, dur=10, state="S"),
            SimpleNamespace(ts=5, dur=10, state="Running"),
            SimpleNamespace(ts=5, dur=0, state="R"),
            SimpleNamespace(ts=0, dur=-2, state="S"),
        ]
        result = state_breakdown(rows, 0, 20)
        self.assertEqual(
            (
                result["Sleeping"],
                result["Overlap"],
                result["Running"],
                result["Unknown"],
            ),
            (5, 5, 5, 5),
        )
        self.assertEqual(sum(result.values()), 20)

    def test_duplicate_same_state_is_ambiguous(self) -> None:
        rows = [SimpleNamespace(ts=0, dur=10, state="S") for _ in range(2)]
        self.assertEqual(state_breakdown(rows, 0, 10)["Overlap"], 10)
