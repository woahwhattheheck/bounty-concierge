import tempfile
from pathlib import Path
import unittest

from legacy_reachability_audit import audit, summarize


class LegacyReachabilityAuditTests(unittest.TestCase):
    def _tree(self, files):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        for rel, body in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return root

    def test_flags_all_primary_legacy_contracts(self):
        root = self._tree({
            "a.hpp": "template <bool legacy_compat = false> void f();\n#include \"ckernel_sfpu_rsqrt_compat.h\"\n",
            "b.py": "cfg = dict(legacy_rsqrt=True)\n",
            "c.cpp": "rsqrt_tile_init<true>(); recip_tile<false>(0);\n",
        })
        summary = summarize(audit(root))
        self.assertEqual(summary["blocking_hits"], 6)
        self.assertEqual(summary["allowed_deprecation_hits"], 0)

    def test_does_not_confuse_independent_controls(self):
        root = self._tree({
            "safe.cpp": "legacy_reduction = true; use_welford = false;\n",
            "safe.hpp": "rsqrt_tile_init(); recip_tile<ReciprocalDestAcc::FP32>(0);\n",
        })
        self.assertEqual(audit(root), [])

    def test_deprecation_shim_is_path_specific(self):
        path = "ttnn/cpp/ttnn/operations/normalization/layernorm/layernorm_nanobind.cpp"
        root = self._tree({path: 'nb::arg("legacy_rsqrt") = false;\n'})
        strict = summarize(audit(root))
        relaxed = summarize(audit(root, allow_deprecation_shim=True))
        self.assertEqual(strict["blocking_hits"], 1)
        self.assertEqual(relaxed["blocking_hits"], 0)
        self.assertEqual(relaxed["allowed_deprecation_hits"], 1)

    def test_same_keyword_outside_shim_still_blocks(self):
        root = self._tree({
            "models/model_config.py": "LayerNormShardedMultiCoreProgramConfig(legacy_rsqrt=True)\n",
            "ttnn/cpp/ttnn/operations/normalization/layernorm/layernorm_nanobind.cpp": 'nb::arg("legacy_rsqrt") = false;\n',
        })
        relaxed = summarize(audit(root, allow_deprecation_shim=True))
        self.assertEqual(relaxed["blocking_hits"], 1)
        self.assertEqual(relaxed["allowed_deprecation_hits"], 1)

    def test_template_boolean_variants_block(self):
        root = self._tree({
            "kernels.cpp": "rsqrt_tile<false, true>(0);\nrecip_tile_init<true, false>();\n",
        })
        rules = {hit.rule for hit in audit(root)}
        self.assertEqual(rules, {"legacy_bool_rsqrt_call", "legacy_bool_recip_init"})

    def test_docs_are_audited_but_build_and_generated_are_not(self):
        root = self._tree({
            "README.md": "legacy_rsqrt=True\n",
            "build/cache.cpp": "legacy_rsqrt=true;\n",
            "generated/api.hpp": "legacy_compat\n",
        })
        hits = audit(root)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "README.md")


if __name__ == "__main__":
    unittest.main()
