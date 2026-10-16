# 真实设备验证

## MT6899 / Dimensity 8400

验证日期：2026-10-16。设备运行 Android API 36、Linux 6.6，使用当前官方 Python 客户端及其 manifest 对应的 Trace Processor v57.2。录制配置设定 10 秒，输入中的有效观察时长约 9827 ms。

通过[官方录制脚本](https://github.com/google/perfetto/blob/main/tools/record_android_trace)采集以下数据源，完整 CLI 执行成功，不需要 MTK 专用解析分支：

- `linux.ftrace`：`sched/sched_switch`、`sched/sched_waking`、`power/cpu_frequency`、`power/cpu_idle`，以及 `gfx`、`view` atrace 类别。
- `linux.process_stats`：开始时扫描进程。
- `android.surfaceflinger.frametimeline`。

| 检查项 | 实采结果 | 核验方法 |
|---|---|---|
| 数据丢失 | `stats` 中没有正值的 `data_loss` 项 | 检查解析诊断；不据此保证所有数据源均完整 |
| CPU 频率 | 9310 个原始样本 | 原始 counter 与 sched 在 Python 中独立求交、积分；143 个进程、361 个线程的运行时间、覆盖时长和估算 cycles 一致 |
| 调度等待 | 143 个进程、356 个线程 | 独立分组和排序 R/R+ 正时长样本，总量、次数、最大值与 nearest-rank P95 一致 |
| CPU idle | 22830 个原始样本、32 个 CPU/状态分组 | 独立积分原始 counter，驻留时间与 idle 退出次数一致，包含零时长转换 |
| FrameTimeline | 6 条完整记录，没有 Unknown 身份类别 | 官方实际时间线表能够查询并正确区分应用与 SF 记录 |

测试期间画面基本静止。FrameTimeline 样本只支持字段与解析兼容性结论，不覆盖持续动画、所有 jank 类别或所有 SurfaceView 路径。该结果不能泛化为所有 MTK 设备或固件；分析其他采集时仍需检查数据源、覆盖率和丢失诊断。

原始设备 trace 和包含应用名称的输出保留在本地，不作为公开测试样例提交。自动化边界测试使用仓库内人工构造的数据。
