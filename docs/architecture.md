# 架构与统计口径

本文定义组件职责、数据流和统计指标。安装、运行参数、绘图依赖及开发命令见 [中文 README](../README_cn.md) 或 [English README](../README.md)。

## 组件与数据流

```text
CLI 参数与 trace 文件
  → PerfettoParser
  → 官方 Perfetto Python 客户端
  → 本地 Trace Processor / 远端 HTTP 服务
  → SQL 原始表与派生视图
  → 终端摘要与可选 VSYNC 直方图
```

| 组件 | 职责 |
|---|---|
| `cli.main()` | 解析参数、选择处理器连接方式、管理分析会话 |
| `PerfettoParser` | 维护时间基准与进程名，组织 SQL 查询和输出 |
| 官方 `perfetto` 客户端 | 管理本地程序的获取与生命周期，或连接远端；通过 protobuf HTTP RPC 装载 trace、查询数据 |
| `cpu_capacity.py` | 提供手选 SoC 的静态相对能力模型，详见 [CPU capacity 定义](cpu-capacity.md) |

普通分析路径依次输出帧节奏、调度状态、FrameMissed、VSYNC、GPU 等待、进程与线程 CPU 时间、CPU 频率和逐 CPU 统计。指定 PID 时仅执行该进程的 CPU 分析。

## 时间与身份基准

内部时间单位为 ns；输出转换为 ms 或 s。全局观察时长取最早与最晚 `sched_slice` 的跨度，末条未结束的调度 slice 按零长度处理；没有调度事件的输入无法通过基础信息检查。

进程和线程分别通过 Perfetto 的 `upid`、`utid` 标识，用户可见的 PID/TID 仅用于展示。FPS 摘要按线程 track 分组；SurfaceView 统计按 counter track 分组。

`Timestamp(s)` 是事件自身时间戳。`TS_View(ms)` 是相对全局起点的偏移，用于定位长间隔；它不保证与所有查看器的显示零点一致。

## FPS 统计指标

| 输出 | 数据来源 | 统计对象 |
|---|---|---|
| FPS Summary | `eglSwapBuffersWithDamageKHR`、`eglSwapBuffers` 和 `postFramebuffer%` slice | 渲染提交或合成相关标记的起点节奏 |
| Render threads stat | 主要渲染线程的两个 EGL slice 名称 | 相邻标记起点间隔、分布和最长间隔 |
| SurfaceView stat | `BufferTX - SurfaceView[%` counter track | counter 正向变化事件的节奏 |
| VSYNC-app/sf | 同名 process counter track | counter 采样时间间隔 |

这些数据源描述提交、合成或埋点变化，不直接测量屏幕呈现时间。`queueBuffer` 不作为独立查询条件。数据是否存在取决于 Android 版本、渲染路径及采集配置；没有匹配项表示缺少该指标的数据。

### 提交节奏摘要

查询 `slice`，经 `thread_track`、`thread` 和 `process` 关联上下文，按 track 聚合。设匹配标记数为 N，首尾开始时间跨度为 T 秒：

```text
FPS = (N − 1) / T
```

N≤1 或 T≤0 时输出 0，表示无法形成有效频率估计。`Total Frames` 是标记数量；`Duration(ms)` 是首尾标记跨度，不是 slice 执行时长或完整 trace 时长。首个标记前和末个标记后的时间不参与该指标。例如 0、16、36 ms 的三个标记得到 `2 / 0.036 ≈ 55.56 FPS`。

FPS>20 且同一 track 不含 `postFramebuffer%` 的条目进入主要渲染线程详情。20 是详情筛选阈值。所有匹配线程均进入调度摘要。

同一 track 的两个 EGL 名称合并计数，未按 surface、buffer 或 frame token 去重；嵌套标记、多 surface 共用线程都可能影响其与真实帧数的对应关系。

### 渲染线程间隔

按 utid 查询 EGL 标记，以 `ts − LAG(ts)` 计算相邻起点间隔。第一个标记没有前驱，不计入直方图。区间采用顺序 CASE：0–4、4–9、9–12、12–16、16–18、超过 18 ms；相邻桶的公共边界归入前一桶。

最长十个间隔用于定位较长停顿。输出时间戳对应间隔结束的标记，分析区间是该标记与其前一个标记之间。固定区间不按刷新率或 deadline 分类，不能据此直接认定 jank。

### SurfaceView counter 间隔

每条匹配 track 独立计算：

1. 按 ts 计算 `value − LAG(value)`，保留差值大于零的事件；首个样本无前值，不参与判断。
2. 对保留事件的时间戳计算相邻间隔，排除无前驱的第一个事件。
3. 输出 `1e9 / AVG(interval_ns)`、有效间隔数量、直方图和最长十个间隔。

`Count` 是间隔数。一次跳增大于 1 仍只算一个正向变化事件；下降、重置和持平不计。少于两个事件或平均间隔不大于零时输出 FPS=0。

所有匹配的 SurfaceView counter 都会参与，与 EGL 统计并列输出，不依据主要渲染线程过滤或自动回退。如果 counter 表示队列深度，其正向变化不能直接等同于 queueBuffer 调用数或显示帧数，需核实设备埋点定义。它与 EGL 结果不能相加。

## FrameTimeline 与呈现指标

EGL、queueBuffer 和 BufferTX 属于调用或队列相关埋点，新系统也可能继续提供。FrameTimeline 是 Android 12 起提供的另一类系统数据源，通过 `android.surfaceflinger.frametimeline` 采集预期与实际帧时间，并提供 token、layer 和呈现／jank 分类。[官方 FrameTimeline 定义](https://perfetto.dev/docs/data-sources/frametimeline)

| 数据 | 用途 | 与提交节奏的区别 |
|---|---|---|
| `expected_frame_timeline_slice` | 帧的预期时间范围 | 定义预期完成时间，不表示某次 EGL 调用 |
| `actual_frame_timeline_slice` | 实际帧时间及 `present_type`、`jank_type` 等信息 | 可区分丢弃、迟到等结果；应用侧的 slice 结束也不能一概当作屏幕呈现时刻 |
| layer、surface/display frame token | 区分 surface 并建立帧间关联 | 一个进程可更新多个 layer，多个 surface 帧可合成为一个 display 帧 |

本工具的 FPS Summary 采用提交标记口径，尚未查询上述 FrameTimeline 表。显示 FPS、应用提交 FPS 和 jank 比例属于不同指标：引入 FrameTimeline 汇总时，应明确选定的进程／layer、app 或 display 帧、统计窗口、丢弃帧处理和去重规则，不能把多个 surface 的帧数直接相加。采集缺失或渲染路径不受支持时，应报告指标不可用。

## 调度、GPU 与 CPU 指标

| 指标 | 计算口径与含义 |
|---|---|
| 调度摘要 | 按 upid/utid 汇总 Running、R/R+ 和指定 D/DK 的 io_wait；sched%=runnable/running，iow%=io_wait/running |
| VSYNC 间隔 | 对同名 counter 的相邻采样求差，不按 value 是否变化过滤；可选直方图过滤不大于 2.5 ms 的间隔，均值不作同样过滤 |
| FrameMissed / hasClientComposition | 对匹配 counter 的 value 直接求和；仅当埋点表示逐次事件值时才能解释为事件数量 |
| GPU completion | 对 `waiting for GPU completion%` slice 的 dur 分桶，反映 CPU 侧等待时长 |
| 进程/线程 CPU 时间 | 汇总 sched 的 dur；CPU Time% 以全局观察时长为分母，跨核并行时可超过 100% |
| 进程 CPU N % | 指定 CPU 的运行时间 / 进程总运行时间 |
| CPU 频率 | 查询 CPU 0/4/7 的 cpufreq counter，以相邻采样间隔加权；占比以全局观察时长为分母 |
| Capa % | 对 CPU 运行时间按选定 SoC 的 capacity × 参考最高频率加权，模型定义见 [CPU capacity 文档](cpu-capacity.md) |

缺失调度样本时相关值为零，不能区分“没有运行”和“没有采集”。GPU 等待时长不等于完整 GPU 执行时长；累计 counter 或状态 counter 的求和也不等于丢帧次数。

## 数据边界

- 全局时间依赖 sched；它不等于完整 trace 的 `trace_bounds`。
- EGL 与 SurfaceView 数据未按 layer/frame 身份关联。
- 窗口函数以 ts 排序，相同时间戳缺少额外排序键；同名 VSYNC track 会合并。
- CPU 展示假定八核；频率仅选择 CPU 0/4/7，且末次采样之后的区间不计入驻留时间。
- 所有指标分析整个输入文件，没有用户指定时间窗口。

## 改进边界

| 问题 | 可行的改进方式 |
|---|---|
| 依赖 sched 估计全局时间 | 使用 Trace Processor 的 `trace_bounds`，让没有调度数据的 trace 仍能分析已有标记 |
| CPU 频率末尾区间缺失 | 每条 counter track 独立排序，将最后一个已知值延续到 trace 结束；首个样本前的未知频率不回填 |
| CPU 数量写死 | 按 trace 中的 CPU ID 输出列与频率，能力模型无法覆盖时显示不可计算 |
| 同时间戳与跨 track 混合 | 用 `(ts, id)` 建立确定顺序，并按 track 分区；这不会恢复采集时已经丢失的真实先后关系 |
| EGL 与 SurfaceView 关联 | 需要稳定的 layer/buffer/frame 身份；只有时间接近或进程名相同不足以认定为同一帧 |
| 显示 FPS 与 jank | 单独定义基于 FrameTimeline 的指标及有效数据条件，不替换现有提交节奏的标签 |
| 缺失埋点或 trace 丢包 | 分析器可以提示数据不足，不能重建未采集的事件 |
