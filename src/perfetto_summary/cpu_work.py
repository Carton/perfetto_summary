"""Runtime-weighted CPU frequency and covered-interval cycle estimates."""

from typing import Any


def cpu_work_rows(
    tp: Any, *, threads: bool = False, pid: int | None = None
) -> list[Any]:
    """Return non-idle entities; unknown frequency is never zero-filled."""
    tp.query("INCLUDE PERFETTO MODULE linux.cpu.frequency")
    tp.query("""
        CREATE PERFETTO TABLE summary_work_sched AS
        SELECT ts, dur, ucpu, utid FROM sched JOIN thread USING (utid)
        WHERE dur > 0 AND NOT is_idle
    """)
    tp.query("""
        CREATE PERFETTO TABLE summary_work_frequency AS
        SELECT ts, dur, ucpu, freq FROM cpu_frequency_counters
        WHERE dur > 0 AND freq IS NOT NULL
    """)
    tp.query("""
        CREATE VIRTUAL TABLE summary_work_intervals USING SPAN_JOIN(
            summary_work_sched PARTITIONED ucpu,
            summary_work_frequency PARTITIONED ucpu)
    """)
    key = "utid" if threads else "upid"
    selection = (
        "thread.tid AS entity_id, thread.name"
        if threads
        else "process.pid AS entity_id, process.name"
    )
    predicate = "" if pid is None else f"AND process.pid = {int(pid)}"
    identity_join = (
        "thread USING (utid) LEFT JOIN process USING (upid)"
        if threads
        else "process USING (upid)"
    )
    try:
        return list(
            tp.query(f"""
            WITH runtime AS (
                SELECT {key}, sum(dur) AS runtime_ns
                FROM summary_work_sched JOIN thread USING (utid)
                GROUP BY {key}
            ), covered AS (
                SELECT {key}, sum(dur) AS covered_ns,
                       sum(1.0 * dur * freq) AS weighted_frequency
                FROM summary_work_intervals JOIN thread USING (utid)
                GROUP BY {key}
            )
            SELECT runtime.{key} AS entity_key, {selection}, runtime_ns,
                   coalesce(covered_ns, 0) AS covered_ns,
                   weighted_frequency / covered_ns AS avg_freq_khz,
                   weighted_frequency / 1e12 AS megacycles,
                   100.0 * coalesce(covered_ns, 0) / runtime_ns AS coverage_pct
            FROM runtime LEFT JOIN covered USING ({key})
            JOIN {identity_join}
            WHERE runtime_ns > 0 {predicate}
            ORDER BY runtime_ns DESC, entity_key
        """)
        )
    finally:
        tp.query("DROP TABLE summary_work_intervals")
        tp.query("DROP TABLE summary_work_frequency")
        tp.query("DROP TABLE summary_work_sched")


def show_cpu_work(tp: Any, pid: int | None = None) -> None:
    """Print busiest processes and threads with frequency coverage."""
    print(
        "\n------------------------\nCPU work estimates (known-frequency runtime only):"
    )
    for threads in (False, True):
        rows = cpu_work_rows(tp, threads=threads, pid=pid)
        print("Threads:" if threads else "Processes:")
        print(
            f"{'ID':>8} {'Name':<30} {'Run(ms)':>12} {'Freq coverage':>14} {'Avg(MHz)':>12} {'Est(Mcycles)':>14}"
        )
        for row in rows[:10]:
            avg = (
                "N/A" if row.avg_freq_khz is None else f"{row.avg_freq_khz / 1000:.3f}"
            )
            cycles = "N/A" if row.megacycles is None else f"{row.megacycles:.3f}"
            print(
                f"{row.entity_id:8} {(row.name or '')[:30]:<30} {row.runtime_ns / 1e6:12.3f} {row.coverage_pct:13.1f}% {avg:>12} {cycles:>14}"
            )
        if not rows:
            print("No non-idle scheduling data.")
    print("Cycles are frequency × covered runtime estimates, not retired instructions.")
