"""Observed CPU idle residency and transitions back to active."""

from typing import Any


def cpu_idle_rows(tp: Any) -> list[Any]:
    """Keep unknown time separate and count only observed idle-to-active edges."""
    tp.query("INCLUDE PERFETTO MODULE linux.cpu.idle")
    return list(
        tp.query("""
        WITH spans AS (
            SELECT *, lag(idle) OVER (
                PARTITION BY track_id ORDER BY ts, id) AS previous_idle
            FROM cpu_idle_counters WHERE dur >= 0 AND idle IS NOT NULL
        ), states AS (
            SELECT cpu, track_id, idle, sum(dur) AS duration_ns,
                   sum(CASE WHEN idle = -1 AND previous_idle >= 0
                            THEN 1 ELSE 0 END) AS exits
            FROM spans GROUP BY cpu, track_id, idle
        )
        SELECT *, 100.0 * duration_ns / (trace_end() - trace_start()) AS trace_pct,
               100.0 * sum(duration_ns) OVER (PARTITION BY track_id)
                   / (trace_end() - trace_start()) AS coverage_pct
        FROM states ORDER BY cpu, track_id, idle
    """)
    )


def show_cpu_idle(tp: Any) -> None:
    """Print state indices without assigning device-specific C-state names."""
    print("\n------------------------\nCPU idle residency:")
    rows = cpu_idle_rows(tp)
    if not rows:
        print("N/A: no CPU idle intervals.")
        return
    print(
        f"{'CPU':>5} {'Track':>7} {'State':>8} {'Time(ms)':>12} {'Trace%':>9} {'Coverage%':>10} {'Idle exits':>11}"
    )
    for row in rows:
        state = "active" if row.idle == -1 else str(row.idle)
        print(
            f"{row.cpu:5} {row.track_id:7} {state:>8} {row.duration_ns / 1e6:12.3f} {row.trace_pct:9.2f} {row.coverage_pct:10.2f} {row.exits:11}"
        )
    print(
        "State indices are device-specific; exits are observed CPU transitions, not process wakeups."
    )
