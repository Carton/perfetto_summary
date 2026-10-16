#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
# Use Perfetto to parse systrace file and do analysis
# Note: The Perfetto python lib is based on python3
#

import argparse
import logging
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from perfetto_summary.cpu_capacity import CpuCapacity, cpu_infos
from perfetto_summary.cpu_idle import show_cpu_idle
from perfetto_summary.cpu_work import show_cpu_work
from perfetto_summary.scheduling import show_scheduling_wait

args: argparse.Namespace

DEFAULT_PARSER = "localhost:9001"
DEFAULT_CPU_TYPE = "sm8250"
MIN_VSYNC_INTERVAL = 1e9 / 400.0
PROC_NAME_MAX_LEN = 50
THREAD_NAME_MAX_LEN = 18
DETAILED_INFO_PROCS_NUM = 10
DETAILED_INFO_THREADS_NUM = 8


def logd(s: object) -> None:
    logging.debug(s)


def print_section() -> None:
    print("\n------------------------")


def ns2ms(ns: float) -> float:
    return ns / 1e6


def ms2ns(ms: float) -> float:
    return ms * 1000000


def ns2s(ns: float) -> float:
    return ns / 1e9


def average(lst: list[float]) -> float:
    if len(lst):
        return sum(lst) / len(lst)
    return 0


def percent(a: float, b: float) -> float:
    if not b:
        return 0.0
    else:
        return a * 100.0 / b


def percent_bar(pct: float, num: int) -> str:
    """Returns maxium `num' of '*' characters for percentage value `pct'"""
    return "*" * (int(pct * num / 100.0))


def durations_to_fpses(durations: Iterable[float]) -> list[float]:
    fpses = []
    for duration in durations:
        if duration > MIN_VSYNC_INTERVAL:
            fpses.append(1e9 / duration)
    return fpses


class ThreadInfo:
    def __init__(self, name: str, upid: int, utid: int, pid: int, tid: int) -> None:
        self.name = name
        self.upid = upid
        self.utid = utid
        self.pid = pid
        self.tid = tid

    def __repr__(self) -> str:
        return " (Name: %s, upid: %d, pid: %d, utid: %d, tid: %d) " % (
            self.name,
            self.upid,
            self.pid,
            self.utid,
            self.tid,
        )


class SchedTime:
    def __init__(self) -> None:
        self.running = 0
        self.runnable = 0
        self.io_wait = 0


class ProcCpuTime:
    def __init__(
        self,
        pid: int,
        upid: int,
        cpu_ms: float,
        cnt: int,
        cpu_ms_per_cpu: dict[int, float],
    ) -> None:
        self.pid = pid
        self.upid = upid
        self.cpu_ms = cpu_ms
        self.cnt = cnt
        self.cpu_ms_per_cpu = cpu_ms_per_cpu


class PerfettoParser:
    def __init__(self, parser: str | None = None) -> None:
        self._parser = parser
        self._tp: Any = None
        self.reset()

    def reset(self) -> None:
        self._procnames: dict[int, str] = {}
        self._procname_len = 0
        self._threadnames: dict[int, str] = {}
        self._start_ts = 0
        self._trace_dur_ns = -1

    def parse(self, trace_file: str) -> None:
        self.reset()
        if self._parser is None:
            self._tp = TraceProcessor(trace=trace_file)
        elif Path(self._parser).is_file():
            self._tp = TraceProcessor(
                trace=trace_file, config=TraceProcessorConfig(bin_path=self._parser)
            )
        elif ":" in self._parser:  # Simple check to make sure it's 'host:port' format
            self._tp = TraceProcessor(trace=trace_file, addr=self._parser)
        else:
            print(
                "Error: no valid perfetto parser specified. Should be 'address:port' format or file path of binary file."
            )
            return

        if not self.parse_basic_info():
            print("Error: Trace basic info parsing failure. Not a valid trace file?")
            return

        # Show basic info first
        self.query_proc_names()
        self.show_trace_duration()

        # If use specify a pid to check in detail, only focus on that process
        if args.pid > 0:
            self.do_cpu_time_per_process(args.pid)
            show_cpu_work(self._tp, pid=args.pid)
            show_scheduling_wait(self._tp, pid=args.pid)
            return

        render_threads = self.do_fps_stat()
        self.do_schedule_stat(render_threads)
        self.do_frame_miss_stat()
        self.do_vsync_stat()
        self.do_gpu_complete_stat()
        self.do_cpu_time_per_process()
        show_cpu_work(self._tp)
        show_scheduling_wait(self._tp)
        self.do_cpu_freq_stats()
        show_cpu_idle(self._tp)
        self.do_per_cpu_stat()

    def do_fps_stat(self) -> list[ThreadInfo]:
        """Do statistics of fps related info.
        Returns list of `ThreadInfo' classes"""

        # A nested EGL wrapper is part of its outer submission, not a second frame.
        self._tp.query("DROP VIEW IF EXISTS frame_slices")
        self._tp.query("""
            CREATE TEMP VIEW frame_slices AS
            WITH RECURSIVE ancestors(frame_id, parent_id) AS (
                SELECT id, parent_id FROM slice
                WHERE name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers')
                UNION ALL
                SELECT a.frame_id, p.parent_id
                FROM ancestors a JOIN slice p ON p.id = a.parent_id
                WHERE p.parent_id IS NOT NULL
            )
            SELECT s.* FROM slice s
            WHERE s.name LIKE 'postFramebuffer%'
               OR (s.name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers')
                   AND NOT EXISTS (
                       SELECT 1 FROM ancestors a JOIN slice p ON p.id = a.parent_id
                       WHERE a.frame_id = s.id AND p.track_id = s.track_id
                         AND p.name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers')
                   ))
        """)

        res_it = self._tp.query("""
            SELECT upid, utid, pid, tid, thread.name AS thread_name, MAX(slice.name LIKE 'postFramebuffer%') AS is_surfaceflinger, count(*) AS cnt,
            (max(ts)-min(ts))/1e9 AS dur_s
            FROM frame_slices AS slice
            JOIN thread_track ON slice.track_id = thread_track.id
            JOIN thread USING(utid)
            JOIN process USING (upid)
            WHERE slice.name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers') OR slice.name LIKE 'postFramebuffer%'
            GROUP BY track_id
            ORDER BY cnt
        """)
        print_section()
        print("FPS Summary:")
        print(
            f"%8s%8s%{self._procname_len}s%18s %15s   %13s   %6s"
            % (
                "Pid",
                "Tid",
                "Proc Name",
                "Thread Name",
                "Total Frames",
                "Duration(ms)",
                "FPS",
            )
        )

        threads = []
        main_render_threads = []
        for row in res_it:
            thread_info = ThreadInfo(
                row.thread_name, row.upid, row.utid, row.pid, row.tid
            )
            threads.append(thread_info)

            if row.cnt <= 1 or row.dur_s <= 0:
                fps = 0
            else:
                fps = (row.cnt - 1) / row.dur_s

            # Find the "Main" render threads: fps > 20, and are not SurfaceFlinger
            if fps > 20 and not row.is_surfaceflinger:
                main_render_threads.append(thread_info)
            print(
                f"%8s%8s%{self._procname_len}s%18s %15d   %13.1f   %06.3f"
                % (
                    row.pid,
                    row.tid,
                    self.get_proc_name(row.upid),
                    row.thread_name,
                    row.cnt,
                    row.dur_s * 1000,
                    fps,
                )
            )
        logd(threads)

        self.do_render_threads_stat(main_render_threads)
        self.do_surfaceview_stat(main_render_threads)

        return threads

    def do_render_threads_stat(self, threads: list[ThreadInfo]) -> None:
        """Do manual histogram on main render threads"""
        print_section()
        print("Render threads stat:")
        print("Note: Timestamp(s) can be used to locate frame in trace file.")
        print(
            "      TS_View here is timestamp relative to start of trace file. i.e. timestamp in Chrome view"
        )
        for thread in threads:
            # Show histogram
            print(
                "\nProc %d (%s) frame duration histogram:"
                % (thread.pid, self.get_proc_name(thread.upid))
            )

            print(
                "%8s%18s%13s%10s%13s"
                % ("Tid", "Thread Name", "Range", "Count", "Avg Dur(ms)")
            )
            self._tp.query("DROP VIEW IF EXISTS thread_durs")
            self._tp.query(
                """
                CREATE TEMP VIEW thread_durs AS
                    SELECT * FROM (
                        SELECT tid, thread.name AS thread_name, ts,
                               ts - LAG(ts) OVER (
                                   PARTITION BY slice.track_id ORDER BY ts, slice.id
                               ) AS dur
                        FROM frame_slices AS slice
                        JOIN thread_track ON slice.track_id = thread_track.id
                        JOIN thread USING(utid)
                        WHERE utid == %s AND slice.name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers')
                    ) WHERE dur IS NOT NULL
                """
                % (thread.utid)
            )

            res_it = self._tp.query("""
                SELECT tid, thread_name,
                CASE
                    WHEN dur between 0 and 4000000 THEN '00~04ms'
                    WHEN dur between 4000000 and 9000000 THEN '04~09ms'
                    WHEN dur between 9000000 and 12000000 THEN '09~12ms'
                    WHEN dur between 12000000 and 16000000 THEN '12~16ms'
                    WHEN dur between 16000000 and 18000000 THEN '16~18ms'
                    WHEN dur > 18000000 THEN '18+ms'
                    ELSE 'Invalid'
                END AS range,
                count(*) AS count,
                avg(dur/1e6) AS avg_dur_ms
                FROM thread_durs
                GROUP BY range
                ORDER BY range
            """)
            for row in res_it:
                print(
                    "%8s%18s%13s%10d%13.1f"
                    % (row.tid, row.thread_name, row.range, row.count, row.avg_dur_ms)
                )

            # Show top 'Janks'
            print(
                "\nProc %d (%s) top duration frames (possibly `Janks'):"
                % (thread.pid, self.get_proc_name(thread.upid))
            )

            print(
                "%8s%18s   %13s   %13s   %13s"
                % ("Tid", "Thread Name", "Timestamp(s)", "TS_View(ms)", "Duration(ms)")
            )
            res_it = self._tp.query(
                """
                SELECT tid, thread_name, ts, dur AS dur_ns FROM thread_durs
                ORDER BY dur DESC, ts
                LIMIT 10
            """
            )
            for row in res_it:
                if row.dur_ns is None:
                    continue
                print(
                    "%8s%18s   %13.6f   %13.3f   %13.1f"
                    % (
                        row.tid,
                        row.thread_name,
                        ns2s(row.ts),
                        ns2ms(row.ts - self.get_start_ts()),
                        ns2ms(row.dur_ns),
                    )
                )

    def do_surfaceview_stat(self, threads: list[ThreadInfo]) -> None:
        """Do manual histogram on those 'BufferTX - SurfaceView[XXX]' counters.
        Sometimes eglSwapBuffers etc. binder calls are missing in trace file and we have to use
        SurfaceView stats instead."""
        print_section()
        print("SurfaceView stat:")
        print("Note: Timestamp(s) can be used to locate frame in trace file.")
        print(
            "      TS_View here is timestamp relative to start of trace file. i.e. timestamp in Chrome view"
        )

        sv_it = self._tp.query(
            "select id,name,upid from process_counter_track where name like 'BufferTX - SurfaceView[%'"
        )
        sv_num = 1
        for sv_row in sv_it:
            self._tp.query("DROP VIEW IF EXISTS sv_durs")
            self._tp.query(
                """
                CREATE TEMP VIEW sv_durs AS
                    SELECT ts - LAG(ts) OVER (ORDER BY ts, id) AS dur, ts FROM
                        (SELECT value - LAG(value) OVER (ORDER BY ts, id) AS vdiff, ts, id
                        FROM counter AS c
                        WHERE c.track_id=%d) AS t
                    WHERE vdiff > 0
                    LIMIT -1 OFFSET 1
                """
                % (sv_row.id)
            )

            # Get average fps
            res_it = self._tp.query(
                "SELECT avg(dur) AS avg_dur, count(dur) AS cnt FROM sv_durs"
            )
            fps = 0.0
            for row in res_it:  # should only contains one row
                if row.avg_dur is not None and row.avg_dur > 0:
                    fps = 1e9 / row.avg_dur

            # Show histogram
            print("\n%d. %s" % (sv_num, sv_row.name))
            print("\nFrame duration histogram (FPS %.3f, Count %d):" % (fps, row.cnt))

            print("%13s%10s%13s" % ("Range", "Count", "Avg Dur(ms)"))

            res_it = self._tp.query("""
                SELECT
                CASE
                    WHEN dur between 0 and 4000000 THEN '00~04ms'
                    WHEN dur between 4000000 and 9000000 THEN '04~09ms'
                    WHEN dur between 9000000 and 12000000 THEN '09~12ms'
                    WHEN dur between 12000000 and 16000000 THEN '12~16ms'
                    WHEN dur between 16000000 and 18000000 THEN '16~18ms'
                    WHEN dur > 18000000 THEN '18+ms'
                    ELSE 'Invalid'
                END AS range,
                count(*) AS count,
                avg(dur/1e6) AS avg_dur_ms
                FROM sv_durs
                GROUP BY range
                ORDER BY range
                """)

            for row in res_it:
                print("%13s%10d%13.1f" % (row.range, row.count, row.avg_dur_ms))

            # Show top 'Janks'
            print("\nTop duration frames (possibly `Janks'):")

            print(
                "%13s   %13s   %13s" % ("Timestamp(s)", "TS_View(ms)", "Duration(ms)")
            )
            res_it = self._tp.query("""
                SELECT * FROM sv_durs
                ORDER BY dur DESC
                LIMIT 10
            """)
            for row in res_it:
                print(
                    "%13.6f   %13.3f   %13.1f"
                    % (
                        ns2s(row.ts),
                        ns2ms(row.ts - self.get_start_ts()),
                        ns2ms(row.dur),
                    )
                )

            sv_num += 1

    def do_cpu_freq_stats(self) -> None:
        """CPU frequencies statistics"""
        print_section()
        print("CPU frequencies stat:")

        self._tp.query("INCLUDE PERFETTO MODULE linux.cpu.frequency")
        for row in self._tp.query(
            "SELECT DISTINCT cpu FROM cpu_frequency_counters ORDER BY cpu"
        ):
            self.do_freq_stat_on(row.cpu)

    def do_freq_stat_on(self, cpu: int) -> None:
        """CPU frequencies statistics on specified cpu"""
        res_it = self._tp.query(
            """
            SELECT freq AS value, sum(dur)/1e6 AS sum_ms, count(*) AS cnt
            FROM cpu_frequency_counters
            WHERE cpu = %d AND freq IS NOT NULL AND dur > 0
            GROUP BY freq
            ORDER BY freq
            """
            % cpu
        )

        # Show results
        if len(res_it) > 0:
            print("\nCPU %d:" % cpu)
            print(
                "%8s %9s %7s %7s %30s"
                % ("Freq", "Dur (ms)", "Count", "Percent", "Percent Bar")
            )
            for row in res_it:
                sum_ms = row.sum_ms or 0
                pct = percent(ms2ns(sum_ms), self.get_trace_duration())
                print(
                    "%8d %9.3f %7d %6.1f%% %30s"
                    % (row.value, sum_ms, row.cnt, pct, percent_bar(pct, 30))
                )

    def _do_sched_stat_on_query(self, res_it: Iterable[Any]) -> dict[int, SchedTime]:
        """Do sched statistics on queried rows. i.e. sched running/runnable/io_wait time summary
        Returns `SchedTime' list"""
        scheds: dict[int, SchedTime] = {}
        for row in res_it:
            if row.key not in scheds:
                scheds[row.key] = SchedTime()
            if row.state == "Running":
                scheds[row.key].running += row.dur_sum_ns
            elif row.state == "R" or row.state == "R+":
                scheds[row.key].runnable += row.dur_sum_ns
            # io_wait condition copied from perfetto android_jank.sql
            elif ((row.state == "D" or row.state == "DK") and row.io_wait == 1) or (
                row.state == "DK" and row.io_wait != 0
            ):
                scheds[row.key].io_wait += row.dur_sum_ns

        return scheds

    def get_ui_threads(self, threads: list[ThreadInfo]) -> list[ThreadInfo]:
        """Get UI threads from ThreadInfo list, ignoring SurfaceFlinger UI threads.
        Returns UI threads ThreadInfo list"""

        ui_threads = []
        res_it = self._tp.query(
            """
            SELECT upid, utid, pid, tid, thread.name AS name
            FROM thread
            JOIN process USING(upid)
            WHERE is_main_thread == 1 AND thread.name != 'surfaceflinger' AND upid IN (%s)
        """
            % (",".join([str(thread.upid) for thread in threads]))
        )
        for row in res_it:
            ui_threads.append(
                ThreadInfo(row.name, row.upid, row.utid, row.pid, row.tid)
            )

        return ui_threads

    def do_schedule_stat(self, threads: list[ThreadInfo]) -> None:
        """Do statistics of schedule stat(scheduled, io_wait, etc.) on specified list of `ThreadInfo'"""
        if not threads:
            return

        # First do stat on processes
        res_it = self._tp.query(
            """
            SELECT upid, upid AS key, sum(dur) AS dur_sum_ns, state, io_wait
            FROM thread_state
            JOIN thread USING(utid)
            WHERE upid IN (%s) and state != 'S'
            GROUP BY upid, state, io_wait
        """
            % (",".join([str(thread.upid) for thread in threads]))
        )
        proc_scheds = self._do_sched_stat_on_query(res_it)

        # Then do stat on threads
        res_it = self._tp.query(
            """
            SELECT utid, utid AS key, sum(dur) AS dur_sum_ns, state, io_wait
            FROM thread_state
            WHERE utid IN (%s) and state != 'S'
            GROUP BY utid, state, io_wait
        """
            % (",".join([str(thread.utid) for thread in threads]))
        )
        thread_scheds = self._do_sched_stat_on_query(res_it)

        print_section()
        print("Render Processes/Threads schedule state summary:")
        print("Note: sched% = runnable / running, iow% = io_wait / running")
        print(
            f"%8s%8s%{self._procname_len}s%18s   %13s %6s %12s   %13s %6s %12s"
            % (
                "Pid",
                "Tid",
                "Proc Name",
                "Thread Name",
                "Proc sched%",
                "iow%",
                "running(ms)",
                "Thread sched%",
                "iow%",
                "running(ms)",
            )
        )
        for thread in threads:
            proc_sched = proc_scheds.get(thread.upid, SchedTime())
            thread_sched = thread_scheds.get(thread.utid, SchedTime())

            print(
                f"%8s%8s%{self._procname_len}s%18s   %12.1f%% %5.1f%% %12d   %12.1f%% %5.1f%% %12d"
                % (
                    thread.pid,
                    thread.tid,
                    self.get_proc_name(thread.upid),
                    thread.name,
                    percent(proc_sched.runnable, proc_sched.running),
                    percent(proc_sched.io_wait, proc_sched.running),
                    ns2ms(proc_sched.running),
                    percent(thread_sched.runnable, thread_sched.running),
                    percent(thread_sched.io_wait, thread_sched.running),
                    ns2ms(thread_sched.running),
                )
            )

        ui_threads = self.get_ui_threads(threads)
        logd(ui_threads)
        self.do_threads_schedule_stat(ui_threads, "UI threads")

    def do_threads_schedule_stat(self, threads: list[ThreadInfo], title: str) -> None:
        """Show thread schedule statistics"""

        res_it = self._tp.query(
            """
            SELECT utid, utid AS key, sum(dur) AS dur_sum_ns, state, io_wait
            FROM thread_state
            WHERE utid IN (%s) and state != 'S'
            GROUP BY utid, state, io_wait
        """
            % (",".join([str(thread.utid) for thread in threads]))
        )
        thread_scheds = self._do_sched_stat_on_query(res_it)

        print_section()
        print("%s schedule state summary:" % (title))
        print("Note: sched% = runnable / running, iow% = io_wait / running")
        print(
            f"%8s%8s%{self._procname_len}s%18s   %13s %6s %12s"
            % (
                "Pid",
                "Tid",
                "Proc Name",
                "Thread Name",
                "Thread sched%",
                "iow%",
                "running(ms)",
            )
        )
        for thread in threads:
            thread_sched = thread_scheds.get(thread.utid, SchedTime())

            print(
                f"%8s%8s%{self._procname_len}s%18s   %12.1f%% %5.1f%% %12d"
                % (
                    thread.pid,
                    thread.tid,
                    self.get_proc_name(thread.upid),
                    thread.name,
                    percent(thread_sched.runnable, thread_sched.running),
                    percent(thread_sched.io_wait, thread_sched.running),
                    ns2ms(thread_sched.running),
                )
            )

    def do_frame_miss_stat(self) -> None:
        """Simple statistics of `FrameMissed' related counters"""

        res_it = self._tp.query("""
            SELECT name, SUM(value) AS sum
            FROM counter
            JOIN process_counter_track ON counter.track_id = process_counter_track.id
            WHERE name like '%%FrameMissed' or name in ('hasClientComposition')
            GROUP BY name
        """)
        print_section()
        print("Frame Miss Summary:")
        for row in res_it:
            print("%s #: %d" % (row.name, row.sum))

    def do_vsync_stat(self) -> None:
        print_section()
        print("VSYNC-app(sf) Summary:")
        self.do_vsync_stat_on("VSYNC-app")
        self.do_vsync_stat_on("VSYNC-sf")

    def do_vsync_stat_on(self, name: str) -> None:
        """Statistics on VSYNC-app(sf) related counters"""

        res_it = self._tp.query(
            """
            SELECT ts - LAG(ts) OVER (
                       PARTITION BY counter.track_id ORDER BY ts, counter.id
                   ) AS dur_ns
            FROM counter
            JOIN process_counter_track ON counter.track_id = process_counter_track.id
            WHERE name = '%s'
            ORDER BY ts
        """
            % (name)
        )
        durs_ns = [row.dur_ns for row in res_it if row.dur_ns is not None]
        avg_dur_ns = average(durs_ns)
        if avg_dur_ns:
            print(
                "%s avg duration: %.1f ms; avg Hz: %.2f"
                % (name, ns2ms(avg_dur_ns), 1e9 / avg_dur_ns)
            )
            if name == "VSYNC-sf":
                self.show_vsync_histogram(name, durs_ns)

    def show_vsync_histogram(self, name: str, durs_ns: list[float]) -> None:
        """Draw histogram on a list of durations (@durs_ns), with the figure title @name"""
        try:
            import matplotlib.pyplot as plt
            import pandas as pd
        except ImportError:
            print(
                "Skipping VSYNC histogram as pandas or matplotlib module not found..."
            )
            return

        # Draw vsync-sf Fps histogram
        df = pd.DataFrame({"%s HZ Histogram" % (name): durations_to_fpses(durs_ns)})
        df.hist(bins=60)
        out_file = "%s_hist.png" % (name)
        logd("Generating %s ..." % (out_file))
        plt.xlabel("HZ")
        plt.ylabel("Count")

        # Workaround Windows generating png file error issue: manually delete file first
        if Path(out_file).exists():
            Path(out_file).unlink()
        plt.savefig(out_file)

    def do_gpu_complete_stat(self) -> None:
        """Do manual histogram of GPU completion duration for each process"""
        res_it = self._tp.query("""
            SELECT upid, pid, tid,
            CASE
                WHEN dur between 0 and 4000000 THEN '00~04ms'
                WHEN dur between 4000000 and 9000000 THEN '04~09ms'
                WHEN dur between 9000000 and 12000000 THEN '09~12ms'
                WHEN dur between 12000000 and 16000000 THEN '12~16ms'
                WHEN dur between 16000000 and 18000000 THEN '16~18ms'
                WHEN dur > 18000000 THEN '18+ms'
                ELSE 'Invalid'
            END AS range,
            count(*) AS count,
            avg(dur/1e6) AS avg_dur_ms
            FROM slice
            JOIN thread_track ON slice.track_id = thread_track.id
            JOIN thread USING(utid)
            JOIN process USING (upid)
            WHERE slice.name LIKE "waiting for GPU completion%" AND dur >= 0
            GROUP BY upid,range
            ORDER BY upid,range
        """)
        print_section()
        print("GPU Completion time simple histogram:")
        print(
            f"%8s%8s%{self._procname_len}s%13s%10s%13s"
            % ("Pid", "Tid", "Proc Name", "Range", "Count", "Avg Dur(ms)")
        )
        for row in res_it:
            print(
                f"%8s%8s%{self._procname_len}s%13s%10d%13.1f"
                % (
                    row.pid,
                    row.tid,
                    self.get_proc_name(row.upid),
                    row.range,
                    row.count,
                    row.avg_dur_ms,
                )
            )

    def do_cpu_time_per_process(self, pid: int | None = None) -> None:
        """Calculate CPU running time for each process"""
        cpu_ids = [
            row.cpu
            for row in self._tp.query(
                "SELECT DISTINCT cpu FROM sched WHERE cpu >= 0 ORDER BY cpu"
            )
        ]
        self._tp.query("DROP VIEW IF EXISTS cpu_time")
        self._tp.query("""
            CREATE TEMP VIEW cpu_time AS
            SELECT upid, sum(dur)/1e6 AS cpu_ms, count(*) AS cnt
            FROM sched
            JOIN thread USING(utid)
            JOIN process USING(upid)
            GROUP BY upid
            ORDER BY cpu_ms DESC LIMIT 30
        """)

        query_pid = ""
        if pid is not None:
            query_pid = "WHERE pid=%d\n" % (pid)
        res_it = self._tp.query(
            """
            SELECT upid, pid, sum(dur)/1e6 AS cpu_ms, cpu, cpu_time.cnt AS cnt, cpu_time.cpu_ms AS total_cpu_ms
            FROM sched
            JOIN thread USING(utid)
            JOIN process USING(upid)
            JOIN cpu_time USING(upid) """
            + query_pid
            + """
            GROUP BY upid,cpu
            ORDER BY cpu_time.cpu_ms DESC, cpu
        """
        )
        print_section()
        print("CPU sched time statistics for each process:")
        print(
            "Note:\n1. Total CPU time and percentage here do not consider CPU differences"
        )
        print("2. Process CPU N % = Process CPU N time / Process total CPU time")
        if pid is not None:
            print(
                "3. Capacity % assumes the selected CPU profile at maximum frequency; N/A means CPU IDs are not covered."
            )
        header = f"%8s%{self._procname_len}s%13s%11s%9s  " % (
            "Pid",
            "Proc Name",
            "CPU Time(ms)",
            "CPU Time%",
            "Count",
        )
        header += "".join(f"{'CPU' + str(cpu) + ' %':>8}" for cpu in cpu_ids)
        if pid is not None:
            header += f"{'Capa %':>8}"
        print(header)

        upids: list[int] = []
        proc_cpu_times = []
        for row in res_it:
            if len(upids) == 0 or row.upid != upids[-1]:
                proc_cpu_times.append(
                    ProcCpuTime(
                        row.pid,
                        row.upid,
                        row.total_cpu_ms,
                        row.cnt,
                        dict.fromkeys(cpu_ids, 0.0),
                    )
                )
                upids.append(row.upid)
            proc_cpu_times[-1].cpu_ms_per_cpu[row.cpu] = row.cpu_ms

        for p in proc_cpu_times:
            print(
                f"%8s%{self._procname_len}s%13.1f%10.1f%%%9s  "
                % (
                    p.pid,
                    self.get_proc_name(p.upid),
                    p.cpu_ms,
                    percent(ms2ns(p.cpu_ms), self.get_trace_duration()),
                    p.cnt,
                ),
                end="",
            )
            for cpu in cpu_ids:
                cpu_ms = p.cpu_ms_per_cpu[cpu]
                print("%8d" % (percent(cpu_ms, p.cpu_ms)), end="")

            # Extra capacity calculation
            if pid is not None:
                capacity = CpuCapacity(args.cpu_type)
                if any(cpu >= len(capacity.cpu_info.caps) for cpu in cpu_ids):
                    print(f"{'N/A':>8}", end="")
                else:
                    total_cap = sum(
                        cpu_ms * capacity.max_cap(cpu)
                        for cpu, cpu_ms in p.cpu_ms_per_cpu.items()
                    ) / ns2ms(self.get_trace_duration())
                    print(
                        "%8.3f" % percent(total_cap, capacity.max_total_cap()), end=""
                    )

            print("")

        upids = upids[: args.procs_num]

        self.do_cpu_time_per_thread(upids)

    def do_cpu_time_per_thread(self, upids: list[int]) -> None:
        """Calculate CPU running time for each thread for top processes"""
        res_it = self._tp.query(
            """
            SELECT upid, pid, tid, thread.name AS threadname, sum(dur)/1e6 AS cpu_ms, count(*) AS total
            FROM sched
            JOIN thread USING(utid)
            JOIN process USING(upid)
            WHERE upid in (%s)
            GROUP BY utid
            ORDER BY upid, cpu_ms DESC
        """
            % (",".join([str(upid) for upid in upids]))
        )

        # Initialized result dicts
        rows: dict[int, list[Any]] = {}
        pids = {}
        for upid in upids:
            rows[upid] = []

        # Pull all data from query
        for row in res_it:
            rows[row.upid].append(row)
            pids[row.upid] = row.pid

        # Show results
        print_section()
        print("Thread CPU sched time statistics details for top processes:")
        for upid in upids:
            print(
                "\nProc %d (%s) top threads:" % (pids[upid], self.get_proc_name(upid))
            )

            print(
                "%8s%18s%13s%11s%9s"
                % ("Tid", "Thread Name", "CPU Time(ms)", "CPU Time%", "Count")
            )
            for row in rows[upid][:DETAILED_INFO_THREADS_NUM]:
                print(
                    "%8s%18s%13.1f%10.1f%%%9s"
                    % (
                        row.tid,
                        row.threadname,
                        row.cpu_ms,
                        percent(ms2ns(row.cpu_ms), self.get_trace_duration()),
                        row.total,
                    )
                )

    def do_per_cpu_stat(self) -> None:
        """Statistics of CPU freq, state, sched time etc. info per CPU"""
        res_it = self._tp.query("""
            SELECT cpu, state, sum(dur)/1e6 AS sum_dur_ms FROM thread_state
            WHERE cpu >= 0
            GROUP BY cpu, state
            ORDER BY cpu
        """)
        print_section()
        print("Per-CPU statistics:")
        print("%8s%13s%11s" % ("CPU", "Run Time(ms)", "Run Time%"))
        for row in res_it:
            print(
                "%8s%13.1f%10.1f%%"
                % (
                    row.cpu,
                    row.sum_dur_ms,
                    percent(ms2ns(row.sum_dur_ms), self.get_trace_duration()),
                )
            )

    def query_proc_names(self) -> None:
        """Query process names and save as upid => name dict, use thread name if no process name
        exists"""

        # 1. Try to use main thread thread name if proc name does not exist
        res_it = self._tp.query("""
            SELECT thread.name AS threadname, process.name AS procname, upid
            FROM thread
            JOIN process USING(upid)
            WHERE (threadname != "" OR procname != "") AND thread.tid == process.pid
        """)

        for row in res_it:
            # Process name can be empty in our captured traces, if so we use main thread
            # (prefered) or other threads that has a thread name as proc name
            if row.procname:
                self._procnames[row.upid] = row.procname[-PROC_NAME_MAX_LEN:]
            else:
                self._procnames[row.upid] = row.threadname[-THREAD_NAME_MAX_LEN:]

        # 2. Some threads even has empty named main thread, use anything thread name as proc name for that case
        res_it = self._tp.query("""
            SELECT thread.name AS threadname, upid, MIN(tid) AS min_tid
            FROM thread
            JOIN process USING(upid)
            WHERE threadname != ""
            GROUP BY upid
        """)

        for row in res_it:
            if row.upid not in self._procnames:
                self._procnames[row.upid] = row.threadname[-THREAD_NAME_MAX_LEN:]

        self._procname_len = max(
            (len(v) for v in self._procnames.values()), default=len("Proc Name")
        )

        # Add some extra spacing
        self._procname_len += 3

    def get_proc_name(self, upid: int) -> str:
        """get proc name from internal _procnames dict"""
        return self._procnames.get(upid, "<unknown>")

    def show_trace_duration(self) -> None:
        print("Trace duration (ms): %d" % (ns2ms(self.get_trace_duration())))

    def parse_basic_info(self) -> bool:
        """Read the full trace interval independently of scheduling availability."""
        for row in self._tp.query(
            "SELECT trace_start() AS start_ts, trace_end() AS end_ts"
        ):
            if (
                row.start_ts is not None
                and row.end_ts is not None
                and row.end_ts > row.start_ts
            ):
                self._start_ts = int(row.start_ts)
                self._trace_dur_ns = int(row.end_ts - row.start_ts)
                return True
        print("Error: trace has no positive-duration time bounds.")
        return False

    def get_start_ts(self) -> int:
        return self._start_ts

    def get_trace_duration(self) -> int:
        return self._trace_dur_ns

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        a: type[BaseException] | None,
        b: BaseException | None,
        c: TracebackType | None,
    ) -> None:
        del a, b, c  # unused
        if self._tp is not None:
            self._tp.close()


def main() -> None:
    global args

    logging.basicConfig(
        level=logging.INFO
    )  # Change this to logging.DEBUG to show some verbose debug logs

    parser = argparse.ArgumentParser(
        description="Parse systrace file using Perfetto trace processor and generate summary/hist figure etc."
    )
    parser.add_argument(
        "-p",
        "--parser",
        dest="parser",
        type=str,
        default=None,
        help="Trace processor binary path, or host:port with --remote",
    )
    parser.add_argument(
        "-r",
        "--remote",
        dest="remote",
        action="store_true",
        default=False,
        help="Whether to use remote trace processor shell, default False.",
    )
    parser.add_argument(
        "-n",
        "--procs",
        dest="procs_num",
        type=int,
        default=DETAILED_INFO_PROCS_NUM,
        help="Number of processes to show detailed threads cpu sched time info",
    )
    parser.add_argument(
        "-P",
        "--pid",
        dest="pid",
        type=int,
        default=-1,
        help="Specify a process pid to check details",
    )
    parser.add_argument(
        "-c",
        "--cputype",
        dest="cpu_type",
        type=str,
        default=DEFAULT_CPU_TYPE,
        choices=sorted(cpu_infos),
        help="CPU capacity reference profile (see docs/cpu-capacity.md)",
    )
    parser.add_argument("file", nargs=1, help="File to parse")
    args = parser.parse_args()
    trace_file = args.file[0]

    if not Path(trace_file).is_file():
        print("Error: %s is not valid file." % (trace_file))
        exit(1)

    shell = args.parser
    if args.remote:
        shell = shell or DEFAULT_PARSER

    with PerfettoParser(shell) as pf_parser:
        pf_parser.parse(trace_file)


if __name__ == "__main__":
    main()
