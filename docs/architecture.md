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

普通分析路径依次输出帧节奏、调度状态、FrameMissed、VSYNC、GPU 等待、进程与线程 CPU 时间、CPU 频率和逐 CPU 统计。指定 PID 时执行该进程的 CPU、调度等待与 FrameTimeline 分析。

## 时间与身份基准

报告首先输出整个 trace 的采集健康摘要，包括实际处理器版本、调度与 FrameTimeline 等数据量、正数 error/data_loss 计数，以及每个 CPU 的频点和 idle 时长覆盖率。覆盖率以全局观察时长为分母，保留缺失值；超过 100% 的值不截断，以暴露重叠区间。idle track 按 CPU 编号与 machine identity 关联，避免合并不同机器的同编号 CPU。活跃非 idle 进程缺少名称或 PID 超出 Linux 范围时单列身份警告，不修改解析结果。没有错误计数不代表采集完整，没有记录也不能证明数据源未启用；指定 PID 时健康摘要仍描述全局输入。

内部时间单位为 ns；输出转换为 ms 或 s。全局起止时间使用 Trace Processor 的 `trace_start()`、`trace_end()`，全局观察时长为两者之差；仅拒绝没有正时长边界的输入。没有 sched 的 trace 仍可分析其已有的 slice 和 counter，调度相关章节则没有数据。

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

查询 `slice`，经 `thread_track`、`thread` 和 `process` 关联上下文，按 track 聚合。同一 track 内的 EGL 调用若有同类 EGL 祖先，则作为外层调用的 wrapper 排除；祖先检查可跨越中间的其他名称。该规则形成最外层 EGL 调用口径，不依据时间重合合并独立调用。设匹配标记数为 N，首尾开始时间跨度为 T 秒：

```text
FPS = (N − 1) / T
```

N≤1 或 T≤0 时输出 0，表示无法形成有效频率估计。`Total Frames` 是标记数量；`Duration(ms)` 是首尾标记跨度，不是 slice 执行时长或完整 trace 时长。首个标记前和末个标记后的时间不参与该指标。例如 0、16、36 ms 的三个标记得到 `2 / 0.036 ≈ 55.56 FPS`。

FPS>20 且同一 track 不含 `postFramebuffer%` 的条目进入主要渲染线程详情。20 是详情筛选阈值。所有匹配线程均进入调度摘要。

两个 EGL 名称归入同一提交标记系列。嵌套 wrapper 只计最外层调用，但未按 surface、buffer 或 frame token 关联；同一线程更新多个 surface 时仍不能直接推算单个 surface 的帧率。

### 渲染线程间隔

按 utid 查询 EGL 标记，每个 track 内按 `(ts, id)` 排序，以 `ts − LAG(ts)` 计算相邻起点间隔。每条 track 的第一个标记没有前驱，不计入直方图；不同 track 不互相作为前驱。区间采用顺序 CASE：0–4、4–9、9–12、12–16、16–18、超过 18 ms；相邻桶的公共边界归入前一桶。

最长十个间隔用于定位较长停顿。输出时间戳对应间隔结束的标记，分析区间是该标记与其前一个标记之间。固定区间不按刷新率或 deadline 分类，不能据此直接认定 jank。

间隔 P95/P99 使用 nearest-rank：对与直方图相同的非负间隔排序，取第 `ceil(p × N)` 项，保留零间隔；无样本时输出 N/A。最长十个正间隔以 `[前一标记起点, 当前标记起点)` 为窗口，仅查询对应 utid 的 thread_state，并将状态裁剪到窗口。Running、Runnable（R/R+）、Sleeping（S）、Blocked（D 开头）、Other、Unknown 和 Overlap 构成互斥时间分区；缺少状态和未覆盖时间计为 Unknown，多条状态同时有效的时间计为 Overlap，不重复累加。未结束状态 `dur=-1` 延续到窗口终点。该分解描述记录中的线程状态，不能仅凭 Sleeping 判断锁、帧率控制或生产者等待等具体原因。

### SurfaceView counter 间隔

每条匹配 track 独立计算：

1. 按 `(ts, id)` 计算 `value − LAG(value)`，保留差值大于零的事件；首个样本无前值，不参与判断。
2. 对保留事件按 `(ts, id)` 计算相邻间隔，排除无前驱的第一个事件。
3. 输出 `1e9 / AVG(interval_ns)`、有效间隔数量、直方图和最长十个间隔。

`Count` 是间隔数。一次跳增大于 1 仍只算一个正向变化事件；下降、重置和持平不计。少于两个事件或平均间隔不大于零时输出 FPS=0。

所有匹配的 SurfaceView counter 都会参与，与 EGL 统计并列输出，不依据主要渲染线程过滤或自动回退。如果 counter 表示队列深度，其正向变化不能直接等同于 queueBuffer 调用数或显示帧数，需核实设备埋点定义。它与 EGL 结果不能相加。

SurfaceView counter 间隔同样输出 nearest-rank P95/P99。counter 未与渲染线程关联，因此不输出推测的线程状态分解。

## FrameTimeline 与呈现指标

EGL、queueBuffer 和 BufferTX 属于调用或队列相关埋点，新系统也可能继续提供。FrameTimeline 是 Android 12 起提供的另一类系统数据源，通过 `android.surfaceflinger.frametimeline` 采集预期与实际帧时间，并提供 token、layer 和呈现／jank 分类。[官方 FrameTimeline 定义](https://perfetto.dev/docs/data-sources/frametimeline)

| 数据 | 用途 | 与提交节奏的区别 |
|---|---|---|
| `expected_frame_timeline_slice` | 帧的预期时间范围 | 定义预期完成时间，不表示某次 EGL 调用 |
| `actual_frame_timeline_slice` | 实际帧时间及 `present_type`、`jank_type` 等信息 | 可区分丢弃、迟到等结果；应用侧的 slice 结束也不能一概当作屏幕呈现时刻 |
| layer、surface/display frame token | 区分 surface 并建立帧间关联 | 一个进程可更新多个 layer，多个 surface 帧可合成为一个 display 帧 |

FPS Summary 保持提交标记口径。FrameTimeline outcomes 独立读取 `actual_frame_timeline_slice`，按 `upid` 和 layer 分组，以全局 trace 为观察窗口，并支持 `--pid`。应用帧仅在 upid、layer 和正数 surface token 齐全时去重；优先保留完整记录，再选最大 slice.id。缺少身份的记录保留独立 slice 身份，并报告 Unkeyed；不依据时间接近合并。SF 记录单列，因为缺少明确 display 身份，不跨记录按 display token 去重，也不据此计算全局显示 FPS。

`dur >= 0` 的去重后记录构成 Complete；`dur < 0` 的原始记录计入 Incomplete，排除在结果分母之外；Duplicate 是移除的应用重复记录数，两者可重叠。Tagged jank 只计官方 `jank_tag` 的 Self Jank／Other Jank；分母为具备已识别标签的完整记录，SurfaceFlinger Stuffing 和 Non-perceivable Jank 计入已知分母、不计 Self／Other 分子；未识别或缺失的标签单列 Unknown。Buffer Stuffing 反映排队高延迟状态，单独存在时不计 Tagged jank。原始 `jank_type` 组合和 `present_type` 按组合计数展示，不把一帧的多个原因重复计为多帧。

Late 与 Dropped 分别来自 `present_type` 的 Late Present／Dropped Frame；它们与 jank 分类可重叠，不能相加。完整记录并不保证预测有效或采集无丢失。本工具不把应用 slice 结束当作屏幕呈现，也不自动关联 EGL／SurfaceView；采集缺失或渲染路径不受支持时输出不可用。数据源与字段定义以[官方文档](https://perfetto.dev/docs/data-sources/frametimeline)为准。

## 调度、GPU 与 CPU 指标

| 指标 | 计算口径与含义 |
|---|---|
| 调度摘要 | 按 upid/utid 汇总 Running、R/R+ 和指定 D/DK 的 io_wait；sched%=runnable/running，iow%=io_wait/running |
| VSYNC 间隔 | 对同名 counter 每条 track 分别按 `(ts, id)` 求相邻采样差，不按 value 是否变化过滤；可选直方图过滤不大于 2.5 ms 的间隔，均值不作同样过滤 |
| FrameMissed / hasClientComposition | 对匹配 counter 的 value 直接求和；仅当埋点表示逐次事件值时才能解释为事件数量 |
| GPU completion | 对 `waiting for GPU completion%` slice 的 dur 分桶，反映 CPU 侧等待时长 |
| 进程/线程 CPU 时间 | 汇总 sched 的 dur；CPU Time% 以全局观察时长为分母，跨核并行时可超过 100% |
| 进程 CPU N % | 指定 CPU 的运行时间 / 进程总运行时间 |
| CPU 频率 | 复用官方 `linux.cpu.frequency` 的 `cpu_frequency_counters`，按 CPU 与频点汇总正时长区间；占比以全局观察时长为分母 |
| Capa % | 对 CPU 运行时间按选定模型的每核静态权重加权；每 MHz 值乘参考最高频率，已归一化的调度器值直接使用，模型定义见 [CPU capacity 文档](cpu-capacity.md) |

缺失调度样本时相关值为零，不能区分“没有运行”和“没有采集”。GPU 等待时长不等于完整 GPU 执行时长；累计 counter 或状态 counter 的求和也不等于丢帧次数。

## 频率区间与 slice 重合

### 调度等待分布

`scheduling.py` 从 `thread_state` 中选择非 idle 线程的 `R`、`R+` 且 `dur > 0` 的区间，分别按 `upid`、`utid` 汇总等待总时长、区间数、最长等待及 P95。`R+` 表示被抢占后仍可运行；两类状态都表示 runnable，不能与实际运行时间相加后称为 CPU 时间。进程汇总为各线程等待段的分布，线程并行等待时总量可超过 trace 时长。

P95 使用 nearest-rank 定义：将 n 段等待按时长升序排列，取第 `ceil(0.95 × n)` 项；不是各线程 P95 的平均值。SQL 窗口排序在 processor 内执行，只传回聚合结果。未结束的 `dur=-1`、零时长及睡眠／阻塞状态不参与统计；没有有效区间时显示 `N/A`。即使区间具有正时长，trace 起点或缺失事件仍可能截断等待，因此结果是采样到的等待段分布，并非完整任务生命周期延迟。

### 运行期间的频率与估算 cycles

`cpu_work.py` 使用官方 `cpu_frequency_counters` 与非 idle、正时长的 sched 区间，按 `ucpu` 做 `SPAN_JOIN`，再按 `upid` 或 `utid` 聚合；PID 仅作筛选，避免 PID 重用导致合并。默认分别展示 CPU 运行时间最多的十个进程和线程，`--pid` 将两类结果限制在指定进程。

运行期间加权频率为 `Σ(交集时长 × 频率) / Σ交集时长`；估算 Mcycles 为 `Σ(时长ns × 频率kHz) / 10¹²`。频率覆盖率为已知频率的运行时长除以全部有效运行时长。迁核后在各 CPU 上分别相交，不跨 CPU 填充；未知频率不作为零频率。没有频率交集时，平均频率与 cycles 输出 `N/A`。cycles 仅覆盖已知频率部分，不外推未覆盖区间，也不代表退休指令数、IPC、能耗或跨架构等价性能。没有调度数据时输出无数据提示。

频率区间采用官方 `linux.cpu.frequency` 模块，不自行补写 LEAD 算法。一个频点从采样时刻保持到下一次采样；末个已知频点保持到 `trace_end()`。首个采样之前的频率未知，不向前填充。同时间戳产生的零时长频点不参与驻留统计；重复的相同值区间汇总到同一频点。Count 表示参与汇总的正时长区间数量。

```text
某频点驻留时间 = Σ该频点的有效区间时长
频点占比 = 驻留时间 / 全局观察时长
```

频率未覆盖完整 trace 时，各频点占比之和可以小于 100%。驻留时间表示频率状态，并不表示 CPU 一直在执行任务；评估工作量还需结合 sched 与 idle。实现依据见[官方频率模块](https://github.com/google/perfetto/blob/v57.2/src/trace_processor/perfetto_sql/stdlib/linux/cpu/frequency.sql)。

slice 的重合应按指标处理：

- 提交节奏：对同类 EGL 祖先关系去除 wrapper，不把执行 dur 相加或求并集。
- GPU 等待直方图：保留每个等待 slice。两个进程并行等待各 5 ms，仍是两个 5 ms 样本，不合并成一个样本。
- 并集占用时间与区间交集：属于另行定义的指标。只有需要“窗口内至少一个任务在执行多久”或“任务运行期间的频率”时，才使用区间并集或相交算法，不能用普通 dur 求和替代。

CPU 时间列按 sched 中实际出现的 CPU ID 生成，频率统计覆盖所有有 cpufreq 数据的 CPU。静态 capacity 模型若不能覆盖观察到的 CPU ID，则输出 `N/A`；模型与设备是否匹配仍需人工核实。

### CPU idle 驻留与退出

复用官方 `linux.cpu.idle` 的 `cpu_idle_counters`，按 CPU、track 与状态编号累计驻留时间。`-1` 为 active，其余编号由设备定义，不能跨平台直接当作相同的 C-state。驻留占比与覆盖率均以全局 trace 时长为分母；首样本前不回填，末状态延续到 trace 结束。active 状态不代表该 CPU 全程执行任务。

按每条 track 的 `(ts, id)` 识别从非负 idle 状态到 active 的退出，包括同时间戳和末端零时长转换；零时长不增加驻留时间。初始 active 没有前驱时不计退出。这里只统计观测到的 CPU 状态转换，不能推导进程唤醒次数或唤醒责任。缺少 idle 数据时显示 `N/A`；系统级 idle 统计不进入 `--pid` 路径。

## 数据边界

- 同一进程多 surface 的标记仍需 layer/frame 身份才能关联，时间接近并不能证明是同一帧。
- `(ts, id)` 使排序可重复，但不能恢复采集时丢失的真实事件先后关系。
- 分析整个输入文件，没有用户指定时间窗口；也不自动识别 SoC capacity 模型。
- 指标有效性依赖采集完整性。缺少频率初始值、帧 token 或 sched 时不能重建未知部分。

## 数据与指标扩展条件

| 指标 | 所需条件 |
|---|---|
| EGL 与 SurfaceView 关联 | 稳定的 layer/buffer/frame 身份；只有时间接近或进程名相同不足以认定为同一帧 |
| 显示 FPS 与 jank | FrameTimeline 数据，以及明确的 layer、时间窗口、去重和丢弃帧规则 |
| 采集完整性判断 | 利用丢包或解析诊断提示数据不足；不能重建未采集的事件 |

## 验证策略

SQL 单元测试覆盖 `(N−1)/T`、零跨度、嵌套 EGL、跨 track 间隔、同时间戳 counter、VSYNC 分区和 CPU ID 超出模型范围。

`tests/fixtures/metrics.systrace` 是人工构造的公开样例，不含设备或应用私有数据。集成测试用官方 Trace Processor 解析它，检查无 sched 的时间边界、末尾频点、单样本、重复频率、相同时间戳及并行 GPU 等待。

真实 trace 回归采用同一版本的官方客户端和二进制分别运行基线与当前代码。FPS、FrameMissed、GPU 样本和进程 CPU 运行时间应在口径不变时一致；全局时间与驻留区间的预期变化分别使用原始 trace 边界与 counter 做独立核算。测试执行流程与依赖见 README。
