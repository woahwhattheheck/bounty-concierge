import importlib.util
import json
import math
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).parents[1] / "work" / "high-value" / "tt-metal-49307" / "command_r_host_oracle.py"
spec = importlib.util.spec_from_file_location("command_r_host_oracle", MODULE_PATH)
oracle = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(oracle)


class CommandRHostOracleTests(unittest.TestCase):
    def assertVectorAlmostEqual(self, left, right, places=12):
        self.assertEqual(len(left), len(right))
        for actual, expected in zip(left, right):
            self.assertAlmostEqual(actual, expected, places=places)

    def test_command_r_issue_dimensions_and_gqa_ratio(self):
        contract = oracle.command_r_contract()
        self.assertEqual(contract["hidden_size"], 8192)
        self.assertEqual(contract["intermediate_size"], 22528)
        self.assertEqual(contract["num_layers"], 40)
        self.assertEqual(contract["num_q_heads"], 64)
        self.assertEqual(contract["num_kv_heads"], 8)
        self.assertEqual(contract["head_dim"], 128)
        self.assertEqual(contract["q_per_kv_group"], 8)
        self.assertAlmostEqual(contract["attention_scale"], 1.0 / math.sqrt(128.0), places=15)

    def test_cohere_qk_norm_is_mean_centered_not_rmsnorm(self):
        hostile = [1.0, 2.0, 3.0, 4.0]
        layer_normed = oracle.cohere_layer_norm(hostile)
        rms_normed = oracle.rms_norm(hostile)
        self.assertAlmostEqual(sum(layer_normed) / len(layer_normed), 0.0, places=12)
        self.assertGreater(sum(rms_normed) / len(rms_normed), 0.1)
        self.assertGreater(oracle.max_abs_diff(layer_normed, rms_normed), 0.5)

    def test_qk_norm_honors_per_dimension_weight(self):
        base = oracle.cohere_layer_norm([1.0, 2.0, 4.0, 8.0])
        weighted = oracle.cohere_layer_norm([1.0, 2.0, 4.0, 8.0], [1.0, 2.0, 3.0, 4.0])
        self.assertVectorAlmostEqual(weighted, [base[i] * (i + 1) for i in range(4)])

    def test_cohere_rope_pairing_rejects_split_half_substitution(self):
        hostile = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(oracle.rotate_half_cohere(hostile), [-2.0, 1.0, -4.0, 3.0])
        self.assertEqual(oracle.rotate_half_split_half(hostile), [-3.0, -4.0, 1.0, 2.0])
        self.assertNotEqual(oracle.rotate_half_cohere(hostile), oracle.rotate_half_split_half(hostile))

    def test_qk_norm_must_precede_rope(self):
        hostile = [1.0, 2.0, 3.0, 4.0]
        cos = [0.0] * 4
        sin = [1.0] * 4
        norm_then_rope = oracle.apply_rope_pairwise(oracle.cohere_layer_norm(hostile), cos, sin)
        rope_then_norm = oracle.cohere_layer_norm(oracle.apply_rope_pairwise(hostile, cos, sin))
        self.assertGreater(oracle.max_abs_diff(norm_then_rope, rope_then_norm), 0.1)

    def test_gqa_expands_each_of_eight_kv_heads_to_eight_q_heads(self):
        kv = [[float(index), float(index) + 0.5] for index in range(8)]
        expanded = oracle.repeat_kv_heads(kv, 64)
        self.assertEqual(len(expanded), 64)
        for kv_index in range(8):
            expected = kv[kv_index]
            self.assertEqual(expanded[kv_index * 8 : (kv_index + 1) * 8], [expected] * 8)

    def test_swiglu_matches_silu_gate_times_up_and_is_stable_for_negative_input(self):
        gate = [-100.0, -1.0, 0.0, 1.0, 100.0]
        up = [2.0, 3.0, 4.0, 5.0, 0.5]
        actual = oracle.swiglu(gate, up)
        expected = [oracle.silu(g) * u for g, u in zip(gate, up)]
        self.assertVectorAlmostEqual(actual, expected)
        self.assertTrue(all(math.isfinite(value) for value in actual))

    def test_logit_scale_is_explicit_post_head_operation(self):
        self.assertEqual(oracle.scale_logits([-2.0, 0.5, 3.0], 0.25), [-0.5, 0.125, 0.75])
        self.assertEqual(oracle.scale_logits([1.0, -1.0], 0.0), [0.0, -0.0])

    def test_invalid_head_ratio_fails_closed(self):
        with self.assertRaises(ValueError):
            oracle.command_r_contract(num_q_heads=64, num_kv_heads=7)
        with self.assertRaises(ValueError):
            oracle.repeat_kv_heads([[1.0], [2.0], [3.0]], 8)

    def test_fixture_report_is_deterministic_json(self):
        first = oracle.fixture_report()
        second = oracle.fixture_report()
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True, allow_nan=False)
        self.assertIn('"q_per_kv_group": 8', encoded)
        self.assertGreater(first["qk_norm"]["max_abs_diff"], 0.5)
        self.assertGreater(first["rope"]["order_max_abs_diff"], 0.1)


if __name__ == "__main__":
    unittest.main()
