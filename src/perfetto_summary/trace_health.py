"""Report available signals and recorded diagnostics without inferring capture health."""

from typing import Any

from perfetto_summary.output import section, table, text


def coverage_rows(tp: Any) -> list[Any]:
    """Keep missing CPU signals distinct from observed zero residency."""
    tp.query("INCLUDE PERFETTO MODULE linux.cpu.frequency")
    tp.query("INCLUDE PERFETTO MODULE linux.cpu.idle")
    return list(
        tp.query("""
        WITH residency AS (
            SELECT ucpu, 'Frequency' AS signal, sum(dur) AS known_ns
            FROM cpu_frequency_counters WHERE dur > 0 AND freq IS NOT NULL
            GROUP BY ucpu
            UNION ALL
            SELECT cpu.id AS ucpu, 'Idle', sum(i.dur)
            FROM cpu_idle_counters i JOIN cpu_counter_track t ON t.id = i.track_id
            JOIN cpu ON cpu.cpu = i.cpu AND cpu.machine_id IS t.machine_id
            WHERE i.dur > 0 AND i.idle IS NOT NULL GROUP BY cpu.id
        ), signals AS (SELECT 'Frequency' AS signal UNION ALL SELECT 'Idle')
        SELECT cpu.id AS ucpu, cpu.cpu, signals.signal, known_ns,
               100.0 * known_ns / nullif(trace_end() - trace_start(), 0) AS coverage_pct
        FROM cpu CROSS JOIN signals LEFT JOIN residency
          ON residency.ucpu = cpu.id AND residency.signal = signals.signal
        ORDER BY cpu.id, signals.signal
    """)
    )


def show_trace_health(tp: Any) -> None:
    """Show global trace diagnostics even when the report selects one PID."""
    section("Trace health (whole trace)")
    status = tp.http.status()
    text(f"Processor: {status.human_readable_version} (RPC {status.api_version})")
    signals = list(
        tp.query("""
        SELECT 'CPU scheduling' AS signal, count(*) AS samples FROM sched
        UNION ALL SELECT 'Thread states', count(*) FROM thread_state
        UNION ALL SELECT 'Slices', count(*) FROM slice
        UNION ALL SELECT 'Actual FrameTimeline', count(*) FROM actual_frame_timeline_slice
    """)
    )
    table(
        ("Signal", "Records", "Availability"),
        [
            (r.signal, r.samples, "recorded" if r.samples else "no records")
            for r in signals
        ],
        right=(1,),
    )
    issues = list(
        tp.query("""
        SELECT name, idx, severity, value FROM stats
        WHERE value > 0 AND severity IN ('error', 'data_loss')
        ORDER BY severity, name, idx
    """)
    )
    table(
        ("Diagnostic", "Index", "Severity", "Value"),
        [(r.name, r.idx, r.severity, r.value) for r in issues],
        right=(1, 3),
        empty="No positive error/data_loss counters recorded.",
    )
    table(
        ("CPU", "CPU identity", "Signal", "Known(ms)", "Coverage%"),
        [
            (
                r.cpu,
                r.ucpu,
                r.signal,
                "N/A" if r.known_ns is None else f"{r.known_ns / 1e6:.3f}",
                "N/A" if r.coverage_pct is None else f"{r.coverage_pct:.2f}",
            )
            for r in coverage_rows(tp)
        ],
        right=(0, 1, 3, 4),
        empty="N/A: no CPU identities recorded.",
    )
    identities = list(
        tp.query("""
        SELECT p.upid, p.pid, p.name,
               CASE WHEN p.pid < 0 OR p.pid >= 4194304 THEN 'PID outside Linux range'
                    ELSE 'missing process name' END AS issue
        FROM process p
        WHERE EXISTS (SELECT 1 FROM sched s JOIN thread t USING(utid)
                      WHERE t.upid = p.upid AND s.dur > 0 AND NOT t.is_idle)
          AND (p.pid < 0 OR p.pid >= 4194304 OR nullif(p.name, '') IS NULL)
        ORDER BY p.upid
    """)
    )
    text(f"Active process identity warnings: {len(identities)} (first 10 shown)")
    table(
        ("UPID", "PID", "Name", "Issue"),
        [(r.upid, r.pid, r.name or "", r.issue) for r in identities[:10]],
        right=(0, 1),
        empty="No active process identity warnings.",
    )
    text(
        "No records does not prove a source was disabled; no errors does not prove a complete capture."
    )
    text(
        "Coverage uses the whole trace window; values above 100% indicate overlapping signal intervals."
    )
