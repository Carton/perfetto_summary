# CPU capacity 参考模型

`--cputype` 为 `--pid` 的 Capa % 选择静态模型。默认仍为 `sm8250`；本次仅补充较新平台，不修改旧模型的数据或计算公式，也不自动识别设备。

```sh
uv run perfetto_summary --pid 1234 --cputype sm8850 capture.perfetto-trace
uv run perfetto_summary --help
```

## 字段与公式

模型的 `caps[i]` 是设备树 CPU 节点的 `capacity-dmips-mhz`，`freqs[i]` 是参考最高频率，单位 kHz。数组按 CPU0、CPU1……排列，不按性能从低到高重新排序。

```text
weight[i] = caps[i] × freqs[i]
Capa % = 100 × Σ(running_time[i] × weight[i]) / (trace_duration × Σweight[i])
```

running_time 与 trace_duration 需使用相同单位。这里按最高频率估算各 CPU 的相对权重，并不读取实际运行频率、热限频或电源策略；它不是精确指令吞吐量、能耗或跨 SoC 跑分。

设备树 `capacity-dmips-mhz` 是每 MHz 的相对能力；按该 binding，在启用 cpufreq 时内核会结合最高频率计算调度器 capacity，无 cpufreq 时则直接归一化设备树原值。因此不能把已经归一化、包含最高频率影响的 `/sys/devices/system/cpu/cpu*/cpu_capacity` 再乘频率后填入 `caps`，否则会重复加权。[内核 binding 定义](https://www.kernel.org/doc/Documentation/devicetree/bindings/arm/cpu-capacity.txt)

## 新增平台

| `--cputype` | CPU ID | capacity-dmips-mhz | 参考最高频率（kHz） | 取值口径 |
|---|---|---:|---:|---|
| `sm8550` | 0–2 / 3–6 / 7 | 326 / 693 / 1024 | 2016000 / 2803200 / 3187200 | 主线 DTS capacity + 对应 CPU OPP 表最大值 |
| `sm8650` | 0–1 / 2–6 / 7 | 1024 / 2909 / 3591 | 2265600 / 3148800 / 3302400 | 主线 DTS capacity + 对应 CPU OPP 表最大值 |
| `sm8750` | 0–5 / 6–7 | 1792 / 1894 | 3530000 / 4320000 | 厂商 DTS capacity + 8 Elite 标准八核版标称频率 |
| `sm8850` | 0–5 / 6–7 | 1792 / 1894 | 3620000 / 4600000 | 厂商 DTS capacity + 8 Elite Gen 5 标准八核版标称频率 |

SM8650 中 CPU2–4 与 CPU5–6 在 DTS 中引用不同 OPP 表，但本次固定版本中两组表的最大值均为 3148800000 Hz。这是所引用主线表的参考上限，不是所有量产 SKU 的实际最高频率；硬件 LUT、speed bin 和设备策略可能进一步限制。

SM8750、SM8850 的 CPU 节点通过 SCMI 关联性能域，固定 DTS 没有给出完整 CPU 最高频点表。本项目的频率值来自官方产品资料，保留其标称精度，例如 3.53 GHz 写为 3530000 kHz，不伪装成已经读取到的精确硬件频点。这两个型号恰好有相同的 DTS capacity 数组，照录各自源码，并非按另一代平台推算。

这些配置仅覆盖八核参考拓扑。4.47/4.74 GHz 等提频版本、七核版本、不同 CPU 编号或拓扑不能无条件使用相同模型。分析具体设备时应核对设备 DT 的 capacity 和 cpufreq policy 的 `cpuinfo_max_freq`；需要精确匹配时按该设备新增独立配置。

## 固定源码来源

查验日期：2026-10-15。仅记录数值与出处，不复制内核实现。

- SM8550：[Linux sm8550.dtsi](https://github.com/torvalds/linux/blob/0fcb05b72344f40806cbe810de8872f1698bf053/arch/arm64/boot/dts/qcom/sm8550.dtsi)，commit `0fcb05b72344f40806cbe810de8872f1698bf053`。逐个 CPU 读取 capacity，并按 `operating-points-v2` 找 `cpu0_opp_table`、`cpu3_opp_table`、`cpu7_opp_table` 的最大 `opp-hz`，除以 1000 得到 kHz。
- SM8650：[Linux sm8650.dtsi](https://github.com/torvalds/linux/blob/0fcb05b72344f40806cbe810de8872f1698bf053/arch/arm64/boot/dts/qcom/sm8650.dtsi)，同一 commit；关联 `cpu0_opp_table`、`cpu2_opp_table`、`cpu5_opp_table`、`cpu7_opp_table`。
- SM8750：[OnePlusOSS sun.dtsi](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750/blob/d50b305f7da9e14715a25120a4ac7b1a4b8b97c3/kernel_platform/qcom/opensource/devicetree/qcom/sun.dtsi)，commit `d50b305f7da9e14715a25120a4ac7b1a4b8b97c3`。CPU0–5 为 1792、CPU6–7 为 1894；频率采用 [Qualcomm SM8750-AB 产品资料](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/documents/Snapdragon-8-Elite-Platform-Product-Brief.pdf)中的 3.53/4.32 GHz。
- SM8850：[OnePlusOSS canoe.dtsi](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8850/blob/5ab2a689ff87d7d28c511f1762cf41c1b90d965a/kernel_platform/qcom/opensource/devicetree/qcom/canoe.dtsi)，commit `5ab2a689ff87d7d28c511f1762cf41c1b90d965a`。CPU0–5 为 1792、CPU6–7 为 1894；频率采用 [Qualcomm 2025 发布资料](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/images/company/news-media/media-center/press-kits/snapdragon-summit-2025-press-kit/day-2-/documents/Snapdragon8EliteGen5_ProductBrief.pdf)中的 3.62/4.6 GHz。

截至查验日，更新的 8 Elite Gen 6 系列已有[官方发布信息](https://www.qualcomm.com/news/releases/2026/09/snapdragon-leads-the-agentic-ai-age-with-two-of-the-world-s-fast)，本次未找到可核实 CPU capacity 与拓扑的公开内核配置，暂不添加猜测值。
