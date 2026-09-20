import math
import unittest

from superforecaster import calibrate_probability, weighted_log_odds


class EnsembleMathTests(unittest.TestCase):
    def test_equal_probabilities_are_unchanged(self):
        self.assertAlmostEqual(weighted_log_odds([0.7, 0.7], [1, 2]), 0.7)

    def test_opposite_probabilities_cancel_in_log_odds(self):
        self.assertAlmostEqual(weighted_log_odds([0.2, 0.8], [1, 1]), 0.5)

    def test_higher_weight_moves_pool_toward_that_forecast(self):
        pooled = weighted_log_odds([0.2, 0.8], [1, 3])
        self.assertTrue(0.5 < pooled < 0.8)

    def test_calibration_shrinks_extremes_but_preserves_direction(self):
        self.assertTrue(0.5 < calibrate_probability(0.8) < 0.8)
        self.assertTrue(0.2 < calibrate_probability(0.2) < 0.5)
        self.assertTrue(math.isclose(calibrate_probability(0.5), 0.5))

    def test_invalid_input_rejected(self):
        with self.assertRaises(ValueError):
            weighted_log_odds([], [])


if __name__ == "__main__":
    unittest.main()
