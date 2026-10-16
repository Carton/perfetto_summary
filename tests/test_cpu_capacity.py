"""Check scheduler-normalized and per-MHz capacity model semantics."""

import unittest

from perfetto_summary.cpu_capacity import CpuCapacity, CpuInfo, cpu_infos


class CpuCapacityTests(unittest.TestCase):
    def test_existing_profiles_keep_frequency_weighting(self) -> None:
        model = CpuCapacity("sm8250")
        self.assertEqual(model.max_cap(0), 1024 * 1804800)
        self.assertEqual(model.max_cap(7), 1894 * 2841600)
        for name, info in cpu_infos.items():
            if name.startswith(("sm", "sxr")):
                with self.subTest(name=name):
                    self.assertFalse(info.normalized)
                    self.assertEqual(
                        CpuCapacity(name).max_total_cap(),
                        sum(c * f for c, f in zip(info.caps, info.freqs)),
                    )

    def test_normalized_snapshot_does_not_weight_frequency_twice(self) -> None:
        model = CpuCapacity("mt6899")
        self.assertEqual(model.max_cap(0), 697)
        self.assertEqual(model.max_cap(4), 929)
        self.assertEqual(model.max_cap(7), 1024)
        self.assertEqual(model.max_total_cap(), 6599)
        # One fully busy prime contributes its share of total device capacity.
        self.assertAlmostEqual(
            100 * model.max_cap(7) / model.max_total_cap(), 15.51750265
        )
        # Concurrent full occupancy of all cores is exactly full device capacity.
        self.assertEqual(
            sum(model.max_cap(cpu) for cpu in range(8)) / model.max_total_cap(), 1
        )

    def test_normalized_weights_are_independent_of_frequency_metadata(self) -> None:
        model = CpuCapacity("mt6899")
        model.cpu_info = CpuInfo(model.cpu_info.caps, (1,) * 8, normalized=True)
        self.assertEqual(model.max_cap(0), 697)
        self.assertEqual(model.max_cap(7), 1024)

    def test_mtk_reference_topologies(self) -> None:
        for name in ("mt6895", "mt6897", "mt6899", "mt6989", "mt6991"):
            with self.subTest(name=name):
                info = cpu_infos[name]
                self.assertEqual(len(info.caps), 8)
                self.assertEqual(len(info.freqs), 8)
                self.assertTrue(all(cap > 0 for cap in info.caps))
                self.assertTrue(all(freq > 0 for freq in info.freqs))
        self.assertEqual(CpuCapacity("mt6897").max_cap(4), 1080 * 3200000)
        self.assertEqual(CpuCapacity("mt6991").max_cap(7), 937 * 3626000)
