# CPU capacity 参考模型

`--cputype` 为 `--pid` 的 Capa % 选择静态模型。默认为 `sm8250`，不自动识别设备；应按 trace 的设备和 CPU 编号手动选择。

```sh
uv run perfetto_summary --pid 1234 --cputype sm8850 capture.perfetto-trace
uv run perfetto_summary --pid 1234 --cputype mt6899 capture.perfetto-trace
uv run perfetto_summary --help
```

## 字段与公式

模型通过 `normalized` 区分两种 capacity：默认 `False` 时，`caps[i]` 是设备树 CPU 节点的 `capacity-dmips-mhz`；`True` 时，`caps[i]` 是已归一化的调度器 capacity。`freqs[i]` 是参考最高频率，单位 kHz；归一化模型中的频率只作为来源元数据，不再次参与权重计算。数组按 CPU0、CPU1……排列，不按性能从低到高重新排序。

```text
weight[i] = caps[i] × freqs[i]   (normalized = False)
weight[i] = caps[i]              (normalized = True)
Capa % = 100 × Σ(running_time[i] × weight[i]) / (trace_duration × Σweight[i])
```

running_time 与 trace_duration 需使用相同单位。每 MHz 模型按参考最高频率估算各 CPU 的相对权重，归一化模型直接使用记录的调度器权重；分析期间不读取实际运行频率、热限频或电源策略。它不是精确指令吞吐量、能耗或跨 SoC 跑分。

设备树 `capacity-dmips-mhz` 是每 MHz 的相对能力；按该 binding，在启用 cpufreq 时内核会结合最高频率计算调度器 capacity，无 cpufreq 时则直接归一化设备树原值。因此不能把已经归一化、包含最高频率影响的 `/sys/devices/system/cpu/cpu*/cpu_capacity` 再乘频率后填入 `caps`，否则会重复加权。[内核 binding 定义](https://www.kernel.org/doc/Documentation/devicetree/bindings/arm/cpu-capacity.txt)

## 高通参考平台

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

## MediaTek 参考平台

查验日期：2026-10-16。选择 D8100、D8300、D8400、D9300、D9400，覆盖 D8400 的上下档与旗舰平台。以下数值仅作对应八核拓扑的静态参考，不跨 SoC 比较绝对分值；除 MT6899 外，尚未实机验证设备实际调度 capacity。

| `--cputype` | 平台 | CPU ID | caps | 频率（kHz） | capacity 口径 |
|---|---|---|---|---|---|
| `mt6895` | Dimensity 8100 | 0–3 / 4–7 | 380 / 1024 | 2000000 / 2850000 | 固定厂商 DTS 的每 MHz 值 |
| `mt6897` | Dimensity 8300 | 0–3 / 4–6 / 7 | 338 / 1080 / 1024 | 2200000 / 3200000 / 3350000 | 固定厂商 DTS 的每 MHz 值 |
| `mt6899` | Dimensity 8400 | 0–3 / 4–6 / 7 | 697 / 929 / 1024 | 2100000 / 3000000 / 3250000 | 实机已归一化的调度器值 |
| `mt6989` | Dimensity 9300 | 0–3 / 4–6 / 7 | 766 / 958 / 1024 | 2000000 / 2850000 / 3250000 | 固定厂商 DTS 的每 MHz 值 |
| `mt6991` | Dimensity 9400 | 0–3 / 4–6 / 7 | 714 / 1024 / 937 | 2400000 / 3300000 / 3626000 | 固定厂商 DTS 的每 MHz 值 |

每 MHz 值不要求以 1024 为上限，也不要求 CPU7 最大；应结合对应频率理解权重。DTS 数据不等同于每台量产设备的运行时 scheduler capacity，也不能根据单个原始值推断厂商进行了覆盖。

### 固定设备树与频率来源

各 DTS 的 CPU 标签、`reg` 和 `performance-domains` 用于核对 CPU 顺序及域归属。它们没有提供足以核实所有硬件 bin 的完整 CPU OPP 上限，频率使用下列参考标称值，不能当作该源码导出的精确频点。不同厂商、提频版、Plus／Ultra／MAX 定制 SKU 或固件版本，应优先核对实际 policy 和 capacity。

- MT6895：[OnePlus MT6895 DTS](https://github.com/OnePlusOSS/android_kernel_5.10_oneplus_mt6895/blob/627e10bcc7a36ce80ca9ac834fde02153f73571d/arch/arm64/boot/dts/mediatek/mt6895.dts)，commit `627e10bcc7a36ce80ca9ac834fde02153f73571d`，发布分支 `oneplus/mt6895_s_12.1_oneplus_ace_race`。每 MHz 值按 CPU0–7 照录；参考频率来自 [MediaTek D8100 规格](https://www.mediatek.com/products/smartphones/mediatek-dimensity-8100)的四个 A55 2.0 GHz 和四个 A78 2.85 GHz。
- MT6897：[MiCode MT6897 DTS](https://github.com/MiCode/MTK_kernel_device_modules/blob/3c4ccd07d5624879555ec710c11bb8818e5cb5c6/arch/arm64/boot/dts/mediatek/mt6897.dts)，commit `3c4ccd07d5624879555ec710c11bb8818e5cb5c6`，分支 `bsp-duchamp-u-oss`。CPU0–3 为 A510、域 0；CPU4–6 为 A715、域 1；CPU7 为 A715、域 2。[MediaTek D8300](https://i.mediatek.com/dimensity-8300/)公布小核 2.2 GHz、大核最高 3.35 GHz；中间三核的 3.2 GHz 参考 [AnTuTu 自有 SoC 数据](https://www.antutu.com/ranking/soc?level=1&order=1)中的 8300-Ultra 项，不冒充 DTS OPP 或本机实测值。
- MT6989：[OPPO MT6989 DTS](https://github.com/oppo-source/android_kernel_modules_oppo_mt6989/blob/4a19f07f0e461456aba577977e1e95540236223b/kernel/kernel_device_modules-6.1/arch/arm64/boot/dts/mediatek/mt6989.dts)，commit `4a19f07f0e461456aba577977e1e95540236223b`，分支 `oppo_mt6989_u_14.0.1_oppo_find_x7`。三个域依次覆盖 CPU0–3、4–6、7。参考频率来自 [D9300 量产设备规格](https://www.vivo.com/en/products/param/x100)的 2.0 / 2.85 / 3.25 GHz；不套用 9300+ 或 9400e 的频率。
- MT6991：[OPPO MT6991 DTS](https://github.com/oppo-source/android_kernel_modules_and_devicetree_oppo_mt6991/blob/a98a9db1584a71c42919a78bfe2de7160f355ff9/kernel/kernel_device_modules-6.6/arch/arm64/boot/dts/mediatek/mt6991.dts)，commit `a98a9db1584a71c42919a78bfe2de7160f355ff9`，分支 `oppo/mt6991_v_15.0.0_oppo_find_x8_pro`。三个域依次覆盖 CPU0–3、4–6、7。参考频率来自 [D9400 量产设备规格](https://www.vivo.com/th/products/param/x200)的 2.4 / 3.3 / 3.626 GHz。

### MT6899 实机快照

该配置来自 2026-10-16 的一台 MT6899 设备：Android API 36、Linux 6.6.89。读取每个 `cpuN/cpu_capacity` 得到 697、697、697、697、929、929、929、1024；`policy0`、`policy4`、`policy7` 的 `related_cpus` 分别为 0–3、4–6、7，`cpuinfo_max_freq` 分别为 2100000、3000000、3250000 kHz。

这里 `normalized=True`，总权重为 `4×697 + 3×929 + 1024 = 6599`，不再乘频率。这是特定设备/固件的参考快照，不保证所有 D8400 的数值相同。以下只读命令可用于核对其他设备：

```sh
adb shell 'for c in /sys/devices/system/cpu/cpu[0-9]*; do echo "$c"; cat "$c/cpu_capacity"; done'
adb shell 'for p in /sys/devices/system/cpu/cpufreq/policy*; do echo "$p"; cat "$p/related_cpus" "$p/cpuinfo_max_freq"; done'
```

### D8200 待补来源

[MT6896 发布仓库](https://github.com/oppo-source/android_kernel_oppo_mt6896/tree/69210632e3b785415656c396a3bd1c54d9c74f61)提供了 `mt6895.dts`，但目前没有核实目标 board 的完整 include/overlay 链与该 SKU 的实际 capacity 关联。因此不直接把共享文件中的数值移植为 `mt6896` 配置；D8200 仅记录为待补平台。
