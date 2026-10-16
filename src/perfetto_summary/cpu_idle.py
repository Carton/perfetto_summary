"""Observed CPU idle residency and transitions back to active."""

from typing import Any

from perfetto_summary.output import bar, section, table, text


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
    section("CPU idle residency")
    rows = cpu_idle_rows(tp)
    if not rows:
        text("N/A: no CPU idle intervals.")
        return
    table(
        (
            "CPU",
            "Track",
            "State",
            "Time(ms)",
            "Trace%",
            "Coverage%",
            "Idle exits",
            "Trace share",
        ),
        [
            (
                r.cpu,
                r.track_id,
                "active" if r.idle == -1 else str(r.idle),
                f"{r.duration_ns / 1e6:.3f}",
                f"{r.trace_pct:.2f}",
                f"{r.coverage_pct:.2f}",
                r.exits,
                bar(r.trace_pct),
            )
            for r in rows
        ],
        right=(0, 1, 3, 4, 5, 6),
    )
    text(
        "State indices are device-specific; exits are observed CPU transitions, not process wakeups."
    )
