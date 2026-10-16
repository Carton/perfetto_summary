# Perfetto Summary

[中文说明](README_cn.md)

A small command-line tool for summarizing Android Perfetto traces: EGL submission cadence, SurfaceView counters, scheduling states, GPU waits, and CPU usage/frequency statistics.

Frame statistics describe the available trace markers, not necessarily displayed FPS. See the [architecture and measurement definitions](docs/architecture.md) before interpreting results.

## Setup and usage

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). The official Perfetto Python package manages the native Trace Processor: on first local use it downloads the prebuilt pinned in its manifest, verifies its SHA-256, and caches it. The first download requires internet access and `curl`; later runs reuse the cache. No native binaries are bundled in this repository.

```sh
uv sync
uv run perfetto_summary capture.perfetto-trace
uv run python -m perfetto_summary --help
```

To generate the optional VSYNC histogram:

```sh
uv sync --extra plots
uv run --extra plots perfetto_summary capture.perfetto-trace
```

Use a custom processor or an existing HTTP server:

```sh
uv run perfetto_summary --parser /path/to/trace_processor_shell capture.perfetto-trace
uv run perfetto_summary --remote --parser localhost:9001 capture.perfetto-trace
```

`--remote` defaults to `localhost:9001`. The trace is sent to the specified server. `--pid` focuses on one process; `--procs` controls detailed process output; `--cputype` selects a CPU capacity reference profile, including `sm8550`, `sm8650`, `sm8750`, and `sm8850`. See the [profile sources and SKU assumptions](docs/cpu-capacity.md).

Host support and the prebuilt version follow the official package manifest. The Python package version and binary version can differ; the installed `perfetto==0.58.2` manifest currently selects v57.2. For offline use without an existing cache, or a host without an official prebuilt, supply a compatible binary with `--parser` or use `--remote`.

## Reading the output

The report uses plain text tables with complete names, right-aligned numbers and no color or ANSI escape sequences. Redirect it with `uv run perfetto_summary trace.perfetto-trace > summary.txt`; redirected tables default to 120 columns and expand if needed to preserve numeric cells. Input control characters are shown as visible escapes. Long names wrap, while numeric cells remain intact. CPU distribution is listed separately from process totals to avoid a wide column per core. `#` bars show the stated sample or trace-time share, capped at 100%; the printed numbers retain their original values.

- FPS Summary measures EGL/compositor marker cadence; SurfaceView stat measures positive counter changes. Neither is a direct displayed-FPS measurement, and their frame counts must not be added together.
- Long frame intervals indicate pauses worth inspecting, not confirmed jank. The listed timestamp marks the end of the interval.
- Missing frame or scheduling events mean the corresponding metric may be unavailable. A blank section does not mean an application's actual FPS is zero.
- CPU Time% can exceed 100% when a process runs on several cores. Capa % is an estimate based on the manually selected SoC profile.
- FrameTimeline outcomes report completed app frames per process/layer, native jank tags, late/dropped counts and incomplete records. Requires Android 12+ and `android.surfaceflinger.frametimeline`. SF timeline records are separate; app completion is not screen presentation. `--pid` also filters this section.
- CPU idle residency shows per-CPU state durations, trace coverage and observed idle exits. State indices are device-specific; exits are CPU transitions, not process wakeups. Requires `power/cpu_idle`.
- CPU work estimates show runtime-weighted frequency, estimated Mcycles and frequency coverage for the busiest processes and threads. Unknown frequency is excluded; cycles cover only the known-frequency runtime and are not retired instructions.
- Scheduling wait reports total runnable time, interval count, nearest-rank P95 and maximum wait for the busiest processes and threads. It measures waiting for CPU, not execution time; missing or truncated intervals affect the result.

For formulas, data sources and limitations, see the [architecture and metric definitions](docs/architecture.md).

MediaTek capacity references are available as `mt6895` (D8100), `mt6897` (D8300), `mt6899` (D8400), `mt6989` (D9300) and `mt6991` (D9400). See the [capacity sources and device-snapshot limits](docs/cpu-capacity.md).

## Development

```sh
uv sync
uv run poe fix-full
uv run poe check-full
uv run poe build
```

Tests include a synthetic trace parsed by the official Trace Processor. Its first use can download the pinned prebuilt; later runs use the cache.

| Task | Purpose |
|---|---|
| `lint` / `lint-fix` | Ruff checks / automatic fixes |
| `format` / `format-check` | Ruff formatting / formatting check |
| `mypy` | Type checking for application code |
| `test` | SQL regression and CLI tests |
| `check-full` | Formatting, lint, types, tests |
| `fix-full` | Lint fixes, formatting, types, tests |
| `build` | Build wheel and source distribution |

The official `perfetto==0.58.2` package supplies the Python client. Application code lives under `src/perfetto_summary/`; the installed command is `perfetto_summary`.

- [Architecture and frame statistics](docs/architecture.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

This project is licensed under [GNU GPL version 3 only (GPL-3.0-only)](LICENSE). Third-party dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
