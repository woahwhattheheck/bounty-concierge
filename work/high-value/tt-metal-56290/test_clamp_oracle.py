import unittest

from clamp_oracle import quantize, requantize, quant_vectors, requant_vectors


class ClampOracleTests(unittest.TestCase):
    def test_uint8_quant_negative_saturates_zero(self):
        for value in ("-1000", "-255.1", "-5", "-0.51", "-0.5", "-0.01"):
            self.assertEqual(quantize(value, "1", 0, "uint8"), 0)

    def test_uint8_quant_upper_saturates_255(self):
        for value in ("255.5", "256", "300", "10000"):
            self.assertEqual(quantize(value, "1", 0, "uint8"), 255)

    def test_uint8_quant_in_range_and_zero_point(self):
        self.assertEqual(quantize("10.2", "0.2", 10, "uint8"), 61)
        self.assertEqual(quantize("-3.1", "0.5", 6, "uint8"), 0)
        self.assertEqual(quantize("100", "0.25", 20, "uint8"), 255)

    def test_round_half_to_even_quantize(self):
        self.assertEqual(quantize("0.5", "1", 0, "uint8"), 0)
        self.assertEqual(quantize("1.5", "1", 0, "uint8"), 2)
        self.assertEqual(quantize("2.5", "1", 0, "uint8"), 2)
        self.assertEqual(quantize("3.5", "1", 0, "uint8"), 4)

    def test_requant_formula_exact(self):
        self.assertEqual(requantize(64, "0.5", 0, "0.25", 10, "uint8"), 138)
        self.assertEqual(requantize(9, "2", 10, "1", 0, "uint8"), 0)
        self.assertEqual(requantize(10, "2", 10, "1", 0, "uint8"), 0)

    def test_requant_uint8_lower_and_upper_saturation(self):
        self.assertEqual(requantize(-1000, "1", 0, "1", 0, "uint8"), 0)
        self.assertEqual(requantize(1000, "1", 0, "1", 0, "uint8"), 255)

    def test_requant_round_half_to_even(self):
        self.assertEqual(requantize(1, "1", 0, "2", 0, "uint8"), 0)
        self.assertEqual(requantize(3, "1", 0, "2", 0, "uint8"), 2)
        self.assertEqual(requantize(5, "1", 0, "2", 0, "uint8"), 2)
        self.assertEqual(requantize(7, "1", 0, "2", 0, "uint8"), 4)

    def test_int8_behavior_remains_signed_and_saturating(self):
        self.assertEqual(quantize("-500", "1", 0, "int8"), -128)
        self.assertEqual(quantize("-12.5", "1", 0, "int8"), -12)
        self.assertEqual(quantize("126.5", "1", 0, "int8"), 126)
        self.assertEqual(quantize("500", "1", 0, "int8"), 127)
        self.assertEqual(requantize(-25, "1", 0, "2", 0, "int8"), -12)
        self.assertEqual(requantize(500, "1", 0, "1", 0, "int8"), 127)

    def test_positive_scale_required(self):
        for bad in ("0", "-1"):
            with self.assertRaises(ValueError):
                quantize("1", bad, 0)
            with self.assertRaises(ValueError):
                requantize(1, bad, 0, "1", 0)
            with self.assertRaises(ValueError):
                requantize(1, "1", 0, bad, 0)

    def test_vector_tables_are_explicit_and_stable(self):
        q = {v.name: v.expected for v in quant_vectors()}
        rq = {v.name: v.expected for v in requant_vectors()}
        self.assertEqual(len(q), 16)
        self.assertEqual(len(rq), 13)
        self.assertEqual(q["u8_far_negative"], 0)
        self.assertEqual(q["u8_overflow"], 255)
        self.assertEqual(rq["u8_negative_source"], 0)
        self.assertEqual(rq["u8_upper_saturation"], 255)

    def test_uint8_sign_symmetry_is_intentionally_broken_by_clamp(self):
        # The bug makes negative values behave like magnitudes. The contract must not.
        self.assertEqual(quantize("-5", "0.1", 0, "uint8"), 0)
        self.assertEqual(quantize("5", "0.1", 0, "uint8"), 50)
        self.assertNotEqual(quantize("-5", "0.1", 0, "uint8"), quantize("5", "0.1", 0, "uint8"))


if __name__ == "__main__":
    unittest.main()
