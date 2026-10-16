# Perfetto Summary

[English](README.md)

一个用于汇总 Android Perfetto trace 的轻量命令行工具，输出 EGL 提交节奏、SurfaceView counter、线程调度状态、GPU 等待及 CPU 使用率和频率统计。

帧统计反映的是 trace 中已有标记的节奏，不一定等于屏幕实际显示 FPS。解读结果前，请先阅读[架构与统计口径说明](docs/architecture.md)。

## 安装与使用

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在项目目录中执行：

```sh
# 安装依赖
uv sync

# 解析 trace
uv run perfetto_summary capture.perfetto-trace

# 查看参数说明；也支持通过 Python 模块启动
uv run python -m perfetto_summary --help
```

官方 Perfetto Python 包负责管理原生 Trace Processor：首次在本地解析时，按其清单下载对应平台的预编译程序，校验 SHA-256 并缓存；后续运行复用缓存。首次下载需要联网，且系统中需要有 `curl`。仓库不再保存或打包原生二进制副本。

支持的主机平台和二进制版本以官方包的清单为准。Python 包版本不一定等于二进制版本：当前安装的 `perfetto==0.58.2` 清单指向 v57.2。

### 生成 VSYNC 直方图

绘图功能需要额外安装 pandas 和 matplotlib：

```sh
uv sync --extra plots
uv run --extra plots perfetto_summary capture.perfetto-trace
```

有相应 VSYNC 数据时，程序会在当前目录生成 `VSYNC-sf_hist.png`。不安装绘图依赖也可输出终端摘要。

### 查看指定进程

```sh
uv run perfetto_summary --pid 1234 --cputype sm8850 capture.perfetto-trace
```

- `--pid`：只输出指定进程的 CPU 分析。
- `--procs`：控制普通摘要中展示线程 CPU 详情的进程数量。
- `--cputype`：为 CPU capacity 估算选择参考模型，默认 `sm8250`；另支持 `sm8350`、`sm8450`、`sm8550`、`sm8650`、`sm8750`、`sm8850` 和 `sxr2130`。各型号数据来源及频率、SKU 假设见 [CPU capacity 参考模型](docs/cpu-capacity.md)。

### 使用自定义程序或远端服务

离线且没有已有缓存时，或官方包没有提供当前平台的预编译程序时，可以指定兼容的 Trace Processor：

```sh
uv run perfetto_summary --parser /path/to/trace_processor_shell capture.perfetto-trace
```

也可以连接已经启动的 HTTP 服务：

```sh
uv run perfetto_summary --remote --parser localhost:9001 capture.perfetto-trace
```

`--remote` 未指定地址时默认连接 `localhost:9001`。trace 会发送给指定服务；远端模式不会下载或启动本地 Trace Processor。

## 解读输出

- FPS Summary 反映 EGL／合成标记节奏，SurfaceView stat 反映 counter 正向变化节奏；两者都不是直接测量的显示 FPS，帧数不能相加。
- 最长帧间隔用于定位值得检查的停顿，不等于已确认的 jank。输出时间戳对应间隔的结束位置。
- 缺少帧或调度事件时，相关指标可能不可用。某个章节没有数据，不表示应用的实际 FPS 为零。
- 进程跨多个 CPU 并行时，CPU Time% 可以超过 100%。Capa % 是基于手选 SoC 模型的估算。
- CPU work estimates 输出繁忙进程和线程运行期间的加权频率、估算 Mcycles 及频率覆盖率。未知频率不补零，cycles 仅覆盖频率已知的运行区间，不代表退休指令数。
- Scheduling wait 输出繁忙进程和线程的 Runnable 等待总时长、段数、P95 和最长等待。它反映等待 CPU 的时间；采集缺失或边界截断会影响结果。

公式、数据来源与限制详见[架构与统计口径](docs/architecture.md)。

## 开发

代码位于 `src/perfetto_summary/`，安装后的命令入口为 `perfetto_summary`。Python 客户端使用官方 `perfetto==0.58.2`，依赖由 `uv.lock` 锁定。

```sh
uv sync
uv run poe fix-full
uv run poe check-full
uv run poe build
```

测试包含由官方 Trace Processor 解析的合成 trace；首次使用可能需要下载清单指定的程序，后续复用缓存。

| 任务 | 用途 |
|---|---|
| `lint` / `lint-fix` | Ruff 检查 / 自动修复 |
| `format` / `format-check` | Ruff 格式化 / 检查格式 |
| `mypy` | 检查应用代码的类型 |
| `test` | 运行 SQL 回归测试及 CLI 测试 |
| `check-full` | 格式、lint、类型检查及测试 |
| `fix-full` | 自动修复 lint、格式化、类型检查及测试 |
| `build` | 构建 wheel 和源码分发包 |

## 相关文档

- [架构与帧统计说明](docs/architecture.md)
- [CPU capacity 参考模型](docs/cpu-capacity.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)
