import unittest
from scripts.runtime_diagnostics import percentile, summarize


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_extreme_value_and_missing_denominator(self):
        values = [10000, 30, 60, 30]
        result = summarize(values, total_titles=5)
        self.assertEqual(values, [10000, 30, 60, 30])
        self.assertEqual(result['coverage_pct'], 80)
        self.assertEqual(result['mean'], 2530)
        self.assertEqual(result['median'], 45)
        self.assertEqual(result['mean_without_largest'], 40)
        self.assertEqual(result['thresholds'][0]['removed_titles'], 1)
        self.assertEqual(result['thresholds'][0]['removed_pct'], 25)
        self.assertEqual(result['thresholds'][0]['mean_retained'], 40)
        self.assertAlmostEqual(result['largest_runtime_share_pct'], 10000 / 10120 * 100)

    def test_percentile_interpolation_and_single_observation(self):
        self.assertEqual(percentile([0, 10], .95), 9.5)
        result = summarize([30], 2)
        self.assertEqual(result['p99'], 30)
        self.assertIsNone(result['mean_without_largest'])

    def test_no_known_runtimes_does_not_impute_zero(self):
        result = summarize([], 4)
        self.assertEqual(result['coverage_pct'], 0)
        self.assertIsNone(result['mean'])
        self.assertIsNone(result['median'])
        self.assertIsNone(summarize([], 0)['coverage_pct'])
