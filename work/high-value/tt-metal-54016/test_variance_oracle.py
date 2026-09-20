import math
import unittest

from variance_oracle import (
    bf16_rne,
    decimal_variance,
    fp32,
    naive_one_pass,
    shifted_two_pass,
    vectors,
)


class VarianceOracleTests(unittest.TestCase):
    def test_translation_invariance_decimal_reference(self):
        base = ["0.0", "0.125", "0.25", "0.375", "0.5"]
        translated = [str(1_000_000 + float(x)) for x in base]
        self.assertEqual(decimal_variance(base), decimal_variance(translated))
        self.assertEqual(decimal_variance(base, 1), decimal_variance(translated, 1))

    def test_population_sample_denominator_relation(self):
        values = [1, 2, 4, 8, 16]
        pop = decimal_variance(values, 0)
        sample = decimal_variance(values, 1)
        self.assertEqual(sample, pop * len(values) / (len(values) - 1))

    def test_shifted_large_offset_matches_reference_and_beats_naive(self):
        values = vectors()[0].values
        ref = float(decimal_variance(values))
        shifted = shifted_two_pass(values)
        naive = naive_one_pass(values)
        self.assertLessEqual(abs(shifted - ref), 1e-7)
        self.assertLess(abs(shifted - ref), abs(naive - ref))

    def test_negative_large_offset_never_negative_variance(self):
        values = vectors()[1].values
        shifted = shifted_two_pass(values)
        naive = naive_one_pass(values)
        self.assertGreaterEqual(shifted, 0.0)
        self.assertLess(naive, 0.0)

    def test_shift_anchor_order_does_not_change_reference_class(self):
        values = [1_000_000.0 + i * 0.125 for i in range(31)] + [999_992.0]
        orders = [values, [values[-1], *values[:-1]], list(reversed(values))]
        reference = float(decimal_variance(values))
        errors = [abs(shifted_two_pass(order) - reference) for order in orders]
        self.assertLessEqual(max(errors), 1e-5)

    def test_constant_is_zero(self):
        values = vectors()[3].values
        self.assertEqual(shifted_two_pass(values), 0.0)
        self.assertEqual(shifted_two_pass(values, 1), 0.0)

    def test_correction_validation(self):
        with self.assertRaises(ValueError):
            shifted_two_pass([], 0)
        with self.assertRaises(ValueError):
            shifted_two_pass([1.0], 1)
        with self.assertRaises(ValueError):
            shifted_two_pass([1.0, 2.0], -1)

    def test_fp32_quantizer_is_idempotent(self):
        for x in [0.0, -0.0, 0.1, 1e6 + 0.125, -65536.015625]:
            self.assertEqual(fp32(fp32(x)), fp32(x))

    def test_bf16_quantizer_is_idempotent_and_finite(self):
        for x in [0.0, -0.0, 0.1, 1.0, 255.5, -1024.25, 1e6]:
            y = bf16_rne(x)
            self.assertTrue(math.isfinite(y))
            self.assertEqual(bf16_rne(y), y)

    def test_bf16_input_fp32_accumulator_smoke(self):
        values = [1024.0 + (i - 16) * 4.0 for i in range(32)]
        result = shifted_two_pass(values, input_quantizer=bf16_rne, accumulator_quantizer=fp32)
        self.assertTrue(math.isfinite(result))
        self.assertGreater(result, 0.0)

    def test_all_published_vectors_shifted_finite_nonnegative(self):
        for vector in vectors():
            for correction in (0, 1):
                result = shifted_two_pass(vector.values, correction)
                self.assertTrue(math.isfinite(result), (vector.name, correction, result))
                self.assertGreaterEqual(result, 0.0, (vector.name, correction, result))


if __name__ == "__main__":
    unittest.main()
