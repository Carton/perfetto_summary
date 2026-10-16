"""Summarize observed runnable intervals without conflating them with CPU time."""

from typing import Any

from perfetto_summary.output import section, table, text


def scheduling_rows(
    tp: Any, *, threads: bool = False, pid: int | None = None
) -> list[Any]:
    """Compute nearest-rank P95 over positive, completed R/R+ intervals."""
    key = "utid" if threads else "upid"
    selection = (
        "thread.tid AS entity_id, thread.name"
        if threads
        else "process.pid AS entity_id, process.name"
    )
    identity_join = (
        "thread USING (utid) LEFT JOIN process USING (upid)"
        if threads
        else "process USING (upid)"
    )
    predicate = "" if pid is None else f"AND process.pid = {int(pid)}"
    return list(
        tp.query(f"""
        WITH waits AS (
            SELECT thread_state.id, {key}, dur,
                   row_number() OVER (PARTITION BY {key} ORDER BY dur, thread_state.id) AS rank,
                   count(*) OVER (PARTITION BY {key}) AS sample_count
            FROM thread_state JOIN thread USING (utid)
            WHERE state IN ('R', 'R+') AND dur > 0 AND NOT is_idle
        ), summary AS (
            SELECT {key}, sum(dur) AS wait_ns, count(*) AS wait_count,
                   max(dur) AS max_wait_ns,
                   max(CASE WHEN rank = (95 * sample_count + 99) / 100 THEN dur END) AS p95_wait_ns
            FROM waits GROUP BY {key}
        )
        SELECT summary.{key} AS entity_key, {selection},
               wait_ns, wait_count, max_wait_ns, p95_wait_ns
        FROM summary JOIN {identity_join}
        WHERE 1 {predicate}
        ORDER BY wait_ns DESC, entity_key
    """)
    )


def show_scheduling_wait(tp: Any, pid: int | None = None) -> None:
    """Print total waiting and distribution of observed runnable intervals."""
    section("Scheduling wait (completed R/R+ intervals)")
    for threads in (False, True):
        rows = scheduling_rows(tp, threads=threads, pid=pid)
        text("Threads:" if threads else "Processes:")
        table(
            ("ID", "Name", "Wait(ms)", "Count", "P95(ms)", "Max(ms)"),
            [
                (
                    r.entity_id,
                    r.name or "",
                    f"{r.wait_ns / 1e6:.3f}",
                    r.wait_count,
                    f"{r.p95_wait_ns / 1e6:.3f}",
                    f"{r.max_wait_ns / 1e6:.3f}",
                )
                for r in rows[:10]
            ],
            right=(0, 2, 3, 4, 5),
            empty="N/A: no positive, completed runnable intervals.",
        )
    text(
        "P95 uses nearest rank; trace boundaries and missing events can truncate waits."
    )
