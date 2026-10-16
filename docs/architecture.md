# 当前项目设计与帧率统计

本文以 `src/perfetto_summary/cli.py` 和官方 `perfetto==0.58.2` Python 包为依据。本文描述现状与限制，不将提交节奏等同于屏幕实际显示帧率。

## 组件与数据流

```text
CLI 参数 / trace 文件
  → PerfettoParser
  → 官方 Perfetto Python 客户端
  → 本地 trace_processor_shell HTTP RPC / 远端 host:port
  → Perfetto SQL 表与临时视图
  → 终端摘要；可选 VSYNC-sf_hist.png
```

`main()` 默认不指定二进制路径，由官方 Python 客户端按平台选择、下载并缓存其清单固定的 Trace Processor，下载时校验 SHA-256。首次下载需要联网且系统提供 `curl`，已有缓存后复用。`--parser` 可以指定二进制；配合 `--remote` 指定服务地址，未指定地址时连接 `localhost:9001`。Python 客户端为本地服务分配端口，启动 `-D --http-port ... --no-ftrace-raw`，传入 trace 后通过 HTTP protobuf RPC 查询；上下文退出时关闭客户端及其启动的本地子进程。

`parse()` 先从最早和最晚的 `sched_slice` 估计 trace 起止时间，缓存进程名。指定 `--pid` 时只输出该进程 CPU 统计；普通路径依次输出 FPS、调度、FrameMissed、VSYNC、GPU completion、进程/线程 CPU、CPU 频率和逐 CPU 统计。分析使用整个输入文件，没有选择时间窗口的参数。时间单位主要为 ns，输出时转换为 ms 或 s。

## 是否使用 queueBuffer

当前入口没有查询名为 `queueBuffer` 的 slice。主路径查询：

```sql
FROM slice
JOIN thread_track ON slice.track_id = thread_track.id
JOIN thread USING(utid)
JOIN process USING(upid)
WHERE slice.name IN ('eglSwapBuffersWithDamageKHR', 'eglSwapBuffers')
   OR slice.name LIKE 'postFramebuffer%'
GROUP BY track_id
```

这些标记处在渲染提交/合成相关路径，但仅凭这些标记不能确定 queueBuffer 调用数、buffer 被消费的时间或屏幕实际呈现时间。不同 Android 版本、渲染后端和采集类别可能不提供这些标记，Vulkan 等路径也可能不被覆盖。没有匹配数据时不应解释为应用 FPS 为零。

## FPS Summary 的计算

每个线程 track 合并匹配的 slice，计数 N，以 slice **开始时间**的最大值减最小值作为观察时长 T，输出 `(N-1)/T`；T 的单位为秒。N≤1 或 T≤0 输出 0，表示不能形成有效频率估计。Total Frames 实际是匹配标记数，Duration 是首尾标记跨度，不是完整 trace 时长，也不是 slice 执行耗时。

例如开始时间为 0、16、36 ms，N=3、T=36 ms，FPS=2/0.036≈55.56。它不是三个瞬时 FPS 的平均值，也不会把首个标记之前和最后一个标记之后的空闲算进去；首尾之间的停顿会影响结果。

FPS>20 且该 track 不包含 `postFramebuffer%` 的条目被视为主要渲染线程，用于详细帧间隔统计。20 是经验筛选阈值，不是性能及格线。所有匹配线程仍传给调度统计。前缀匹配用于兼容带后缀的 SurfaceFlinger 标记；它不能识别所有 SurfaceFlinger 渲染路径。

同一 track 的两个 EGL 名称合并计数，未按 surface、layer、buffer ID 或 frame token 去重。如果实现同时产生嵌套标记，或同一线程操作多个 surface，就可能高估某一 surface 的帧率。

## Render threads stat

对主要渲染线程，按 utid 查询两个 EGL 名称，以 `ts-LAG(ts) OVER (ORDER BY ts)` 得到相邻提交起点间隔。第一个样本没有前驱，不参与直方图。分桶为 0–4、4–9、9–12、12–16、16–18、超过 18 ms，桶边界由顺序 CASE/BETWEEN 决定：例如 4 ms 落在第一桶，9 ms 落在第二桶。

另外按间隔倒序输出前十项。Timestamp 是当前（间隔结束）标记的 trace 时间戳，TS_View 是减去本项目估计的最早 sched 时间后得到的 ms。定位长间隔应查看当前标记与前一个标记之间的区间。这个相对零点未必等于所有查看器的零点。

长间隔仅提示“可能卡顿”，不是正式 jank 判定；固定桶没有适配 60/90/120 Hz、动态刷新率或平台 deadline。

## SurfaceView counter 路径

`do_surfaceview_stat()` 总会执行，遍历所有名称匹配 `BufferTX - SurfaceView[%` 的 `process_counter_track`，并非仅在 EGL 缺失时回退，也未按主要渲染线程过滤；目前 `threads` 参数未用于过滤。

每条 counter track 独立执行：

1. 对全部 counter 样本按 ts 排序，计算 `vdiff=value-LAG(value)`。
2. 仅保留 `vdiff>0` 的时间戳，首个样本因差值为 NULL 被排除。
3. 在这些正向变化事件之间计算相邻时间差，再排除没有前驱的第一个事件。
4. 以 `1e9/avg(dur)` 输出 FPS，并输出直方图和最长十个间隔。

因此它测量的是 counter **正向变化事件**的节奏。即便 value 一次增加 3，也只算一个事件；下降、重置和持平都不算帧。如果 counter 表示队列深度，连续入队/出队与采样方式会影响正向变化，不能从名称直接推断等于 queueBuffer 或显示帧数。必须结合该设备埋点语义核实。

这里 Count 是有效间隔数 K，通常对应 K+1 个正向事件，不是 counter 样本数。少于两个正向事件时 Count=0、FPS=0，不再因 SQL AVG 返回 NULL 崩溃。所有间隔为零时也输出 0。不要把这个结果与 EGL 结果直接相加。

## 相关摘要如何解释

- 调度统计按 upid/utid 汇总 thread_state：Running、R/R+、指定 D/DK 的 io_wait。sched%=runnable/running，iow%=io_wait/running，不是占 trace 时长的比例。缺少调度样本时输出零；零也可能意味着数据缺失。
- VSYNC-app/sf 使用同名 counter 的相邻时间差，不按 value 是否改变过滤。同名多 track 目前会合并，可能产生偏差。可选直方图剔除不大于 2.5 ms（400 Hz）的间隔，平均 VSYNC 数值则未作同样过滤。
- FrameMissed/hasClientComposition 目前直接求 counter value 的和，依赖埋点本身是否为逐次事件值；若为累计计数或状态，不能解释为丢帧总数。
- GPU completion 使用等待 slice 的 dur，反映 CPU 侧等待时长，不等于完整 GPU 执行耗时。
- CPU 频率目前只选 CPU 0/4/7；进程 CPU 输出假定八核，capacity 依赖手选 SoC 模型。不能将这些默认值直接推广到任意硬件；可选 SoC、取值来源和频率假设见 [CPU capacity 参考模型](cpu-capacity.md)。

## 已知限制与后续方向

当前工具适合检查旧式 systrace 标记和提交节奏。更准确的呈现与 jank 分析需要设备提供 FrameTimeline、SurfaceFlinger layer/frame token 或对应平台指标，并定义时间窗口和 layer 归属；这属于后续功能，本次未更换统计口径。

其他已知限制包括：必须有 sched_slice 才认可输入；trace 时长不是完整 trace_bounds；SurfaceView 和 EGL 未相互关联；相同时间戳没有额外排序键；CPU 固定拓扑和末尾频率区间未完整覆盖。解释结果前应确认采集类别、埋点是否齐全以及是否混入多个 surface。

## 运行与验证

项目要求 Python 3.12+。官方客户端固定为 `perfetto==0.58.2`；依赖由 `uv.lock` 锁定。原生二进制版本由客户端的 prebuilt 清单决定，不等同于 Python 包版本；当前安装包的清单指向 v57.2。仓库不再随附二进制。可选 `plots` extra 安装 pandas/matplotlib，仅用于 VSYNC 图。

```sh
uv sync
uv run perfetto_summary capture.perfetto-trace
uv run poe check-full
```

离线且没有缓存时，可用 `--parser` 指定现有二进制；`--remote` 连接现有服务，不使用本地下载流程。`uv run poe fix-full` 执行自动 lint 修复、格式化、类型检查和测试；构建使用 `uv run poe build`。

## Python 包与官方客户端

```text
pyproject.toml / uv.lock
src/perfetto_summary/
  __init__.py
  __main__.py             python -m perfetto_summary
  cli.py                  perfetto_summary 命令与 SQL 分析
  cpu_capacity.py         现有 SoC capacity 模型
```

原先复制的顶层 `perfetto/` 目录已移除，避免遮蔽 site-packages 中的官方包。当前通过公开的 `TraceProcessor(trace=..., config=...)` / `TraceProcessor(trace=..., addr=...)` 接口加载 trace，不再维护 protobuf descriptor、RPC 启动和下载代码的私有副本。入口同时支持 `uv run perfetto_summary` 和 `uv run python -m perfetto_summary`，原根目录脚本已迁入包中。

官方接口与安装说明：[Python API](https://perfetto.dev/docs/analysis/trace-processor-python)、[0.58.2 发行包](https://pypi.org/project/perfetto/0.58.2/)。默认调用 `TraceProcessor(trace=...)`，使用官方客户端的下载与缓存机制；自定义 `--parser` 时才传入 `TraceProcessorConfig(bin_path=...)`。后续升级客户端和二进制时应共同验证 SQL 行为。
