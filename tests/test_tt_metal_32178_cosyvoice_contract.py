import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "work" / "high-value" / "tt-metal-32178" / "host_fixture_contract.py"
SPEC = importlib.util.spec_from_file_location("tt_metal_32178_cosyvoice_contract", MODULE_PATH)
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class CosyVoiceHostContractTest(unittest.TestCase):
    def test_static_contract(self):
        contract.validate_contract()

    def test_every_mode_has_a_minimal_valid_fixture(self):
        for mode, item in contract.MODE_CONTRACTS.items():
            contract.validate_mode_fixture(mode, item.required_inputs)

    def test_missing_mode_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing required inputs"):
            contract.validate_mode_fixture("zero_shot", frozenset({"text", "prompt_audio"}))

    def test_unknown_mode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown mode"):
            contract.validate_mode_fixture("singing", frozenset())

    def test_cross_lingual_does_not_reintroduce_prompt_text_to_llm(self):
        item = contract.MODE_CONTRACTS["cross_lingual"]
        self.assertTrue(item.uses_llm)
        self.assertFalse(item.llm_prompt_text_present)

    def test_instruct_does_not_reintroduce_llm_speaker_embedding(self):
        item = contract.MODE_CONTRACTS["instruct"]
        self.assertTrue(item.uses_llm)
        self.assertFalse(item.llm_embedding_present)

    def test_voice_conversion_bypasses_llm_generation(self):
        item = contract.MODE_CONTRACTS["voice_conversion"]
        self.assertFalse(item.uses_llm)
        self.assertEqual(item.handoffs, ("frontend", "flow", "hift"))

    def test_handoff_shapes_keep_token_mel_and_waveform_boundaries_distinct(self):
        self.assertEqual(contract.HANDOFFS["llm_to_flow"]["dtype"], "int32")
        self.assertEqual(
            contract.HANDOFFS["prompt_feat_to_flow"]["shape"],
            "[batch, prompt_time, 80]",
        )
        self.assertEqual(
            contract.HANDOFFS["flow_to_hift"]["shape"],
            "[batch, 80, mel_time]",
        )
        self.assertEqual(
            contract.HANDOFFS["hift_to_output"]["shape"],
            "[batch, waveform_time]",
        )

    def test_streaming_cache_constants_remain_explicit(self):
        self.assertEqual(contract.TOKEN_OVERLAP_LEN, 20)
        self.assertEqual(contract.MEL_CACHE_LEN, 20)
        self.assertEqual(contract.SOURCE_CACHE_LEN, 20 * 256)

    def test_host_packet_cannot_grant_hardware_or_payment_authority(self):
        for forbidden in sorted(contract.FORBIDDEN_HOST_CLAIMS):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "overclaims authority"):
                    contract.validate_claims({forbidden: True})
        contract.validate_claims({key: False for key in contract.FORBIDDEN_HOST_CLAIMS})


if __name__ == "__main__":
    unittest.main()
