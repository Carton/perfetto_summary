#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
# Util lib to query CPU capacity info

sm8250_freqs = (1804800, 1804800, 1804800, 1804800, 2419200, 2419200, 2419200, 2841600)
# Note: CPU capacities come from QCOM dtsi file(qcom/kona.dtsi etc.) capacity-dmips-mhz
# config item, which represents CPU capacity expressed in normalized DMIPS/MHz value.
sm8250_caps = (1024, 1024, 1024, 1024, 1894, 1894, 1894, 1894)

sm8350_caps = (1024, 1024, 1024, 1024, 1946, 1946, 1946, 2048)

# FIXME: Note that sm8450 may have early revisions that cannot reach this max freq
sm8450_freqs = (1785600, 1785600, 1785600, 1785600, 2419200, 2419200, 2419200, 2841600)
sm8450_caps = (1024, 1024, 1024, 1024, 2253, 2253, 2253, 2386)


# CPU-ID order; frequencies are in kHz. Sources and SKU assumptions are
# documented in docs/cpu-capacity.md. Keep legacy profiles unchanged.
# Mainline capacity-dmips-mhz and maximum CPU OPP frequencies.
sm8550_caps = (326, 326, 326, 693, 693, 693, 693, 1024)
sm8550_freqs = (2016000, 2016000, 2016000, 2803200, 2803200, 2803200, 2803200, 3187200)
sm8650_caps = (1024, 1024, 2909, 2909, 2909, 2909, 2909, 3591)
sm8650_freqs = (2265600, 2265600, 3148800, 3148800, 3148800, 3148800, 3148800, 3302400)

# Vendor capacity-dmips-mhz; standard eight-core SKU nominal frequencies.
# SCMI supplies the actual device frequency limits at runtime.
sm8750_caps = (1792, 1792, 1792, 1792, 1792, 1792, 1894, 1894)
sm8750_freqs = (3530000, 3530000, 3530000, 3530000, 3530000, 3530000, 4320000, 4320000)
sm8850_caps = (1792, 1792, 1792, 1792, 1792, 1792, 1894, 1894)
sm8850_freqs = (3620000, 3620000, 3620000, 3620000, 3620000, 3620000, 4600000, 4600000)


class CpuInfo:
    def __init__(self, caps: tuple[int, ...], freqs: tuple[int, ...]) -> None:
        self.caps = caps
        self.freqs = freqs


cpu_infos = {
    "sm8250": CpuInfo(sm8250_caps, sm8250_freqs),
    "sxr2130": CpuInfo(sm8250_caps, sm8250_freqs),
    "sm8350": CpuInfo(sm8350_caps, sm8250_freqs),
    "sm8450": CpuInfo(sm8450_caps, sm8450_freqs),
    "sm8550": CpuInfo(sm8550_caps, sm8550_freqs),
    "sm8650": CpuInfo(sm8650_caps, sm8650_freqs),
    "sm8750": CpuInfo(sm8750_caps, sm8750_freqs),
    "sm8850": CpuInfo(sm8850_caps, sm8850_freqs),
}


class CpuCapacity:
    @staticmethod
    def valid_type(cpu_type: str) -> bool:
        return cpu_type in cpu_infos

    def __init__(self, cpu_type: str) -> None:
        self.cpu_info = cpu_infos[cpu_type]

        self._max_total_cap = 0
        for cap, freq in zip(self.cpu_info.caps, self.cpu_info.freqs):
            self._max_total_cap += cap * freq

    """ Returns maximum capacity for specified cpu id.
    @cpu_id: cpu id. typically range from 0~7"""

    def max_cap(self, cpu_id: int) -> int:
        return self.cpu_info.caps[cpu_id] * self.cpu_info.freqs[cpu_id]

    def max_total_cap(self) -> int:
        return self._max_total_cap
