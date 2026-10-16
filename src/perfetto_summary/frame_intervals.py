"""Frame submission interval distributions and observed thread-state coverage."""

from collections import defaultdict
from typing import Any

from perfetto_summary.output import table, text


def interval_percentiles(tp: Any, *, surfaceview: bool = False) -> Any:
    """Use nearest rank, including zero gaps, over track-local intervals."""
    source = "sv_durs" if surfaceview else "thread_durs"
    return list(
        tp.query(f"""
        WITH ranked AS (
            SELECT dur, row_number() OVER (ORDER BY dur) AS rank,
                   count(*) OVER () AS n
            FROM {source} WHERE dur >= 0
        )
        SELECT count(*) AS samples,
               max(CASE WHEN rank = (95 * n + 99) / 100 THEN dur END) AS p95_ns,
               max(CASE WHEN rank = (99 * n + 99) / 100 THEN dur END) AS p99_ns
        FROM ranked
    """)
    )[0]


def state_breakdown(rows: list[Any], start: int, end: int) -> dict[str, int]:
    """Partition a half-open window; ambiguous overlapping states stay explicit."""
    events: dict[int, list[tuple[str, int]]] = defaultdict(list)
    events[start]
    events[end]
    for row in rows:
        if row.dur < -1 or row.dur == 0:
            continue
        left = max(start, row.ts)
        right = min(end, end if row.dur == -1 else row.ts + row.dur)
        if left < right:
            state = row.state or ""
            events[left].append((state, 1))
            events[right].append((state, -1))
    totals = dict.fromkeys(
        ("Running", "Runnable", "Sleeping", "Blocked", "Other", "Unknown", "Overlap"), 0
    )
    active: dict[str, int] = defaultdict(int)
    previous = start
    for ts in sorted(events):
        count = sum(active.values())
        category = "Unknown" if count == 0 else "Overlap"
        if count == 1:
            state = next(key for key, value in active.items() if value)
            category = {
                "Running": "Running",
                "R": "Runnable",
                "R+": "Runnable",
                "S": "Sleeping",
                "": "Unknown",
            }.get(state, "Blocked" if state.startswith("D") else "Other")
        totals[category] += ts - previous
        for state, delta in events[ts]:
            active[state] += delta
        previous = ts
    return totals


def show_interval_percentiles(tp: Any, *, surfaceview: bool = False) -> None:
    """Print ranks for the selected submission or counter interval series."""
    row = interval_percentiles(tp, surfaceview=surfaceview)
    text("Interval percentiles (nearest rank; same samples as histogram):")
    table(
        ("Samples", "P95(ms)", "P99(ms)"),
        [
            (
                row.samples,
                "N/A" if row.p95_ns is None else f"{row.p95_ns / 1e6:.3f}",
                "N/A" if row.p99_ns is None else f"{row.p99_ns / 1e6:.3f}",
            )
        ],
        right=(0, 1, 2),
    )


def show_interval_details(tp: Any, utid: int, trace_start: int) -> None:
    """Analyze the same track-local gaps as the existing histogram."""
    show_interval_percentiles(tp)
    gaps = list(
        tp.query("""
        SELECT ts, dur FROM thread_durs WHERE dur > 0
        ORDER BY dur DESC, ts LIMIT 10
    """)
    )
    display = []
    for gap in gaps:
        start, end = gap.ts - gap.dur, gap.ts
        states = list(
            tp.query(f"""
            SELECT ts, dur, state FROM thread_state
            WHERE utid = {int(utid)} AND ts < {int(end)}
              AND (dur = -1 OR ts + dur > {int(start)})
        """)
        )
        totals = state_breakdown(states, start, end)
        display.append(
            (
                f"{(end - trace_start) / 1e6:.3f}",
                *[f"{duration / 1e6:.3f}" for duration in totals.values()],
            )
        )
    text("Longest intervals: thread state time (ms), ending at TS_View.")
    table(
        (
            "End TS_View(ms)",
            "Running",
            "Runnable",
            "Sleeping",
            "Blocked",
            "Other",
            "Unknown",
            "Overlap",
        ),
        display,
        right=tuple(range(8)),
    )
    text(
        "State windows are [previous marker, marker); unfinished states are clipped to the window. Sleeping alone does not identify a cause."
    )
