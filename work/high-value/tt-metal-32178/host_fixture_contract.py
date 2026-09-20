"""Host-only CosyVoice acceptance contract for tenstorrent/tt-metal#32178.

No Torch, TTNN, audio runtime, hardware, network, or model-weight dependency.
The goal is to make source-derived handoffs explicit before device work starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Mapping, Tuple

TT_METAL_COMMIT = "708e7f58aef9d089f0e5097d4a772c7c8ee129ea"
COSYVOICE_COMMIT = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
PIPELINE = ("frontend", "llm", "flow", "hift")
MEL_CHANNELS = 80
TOKEN_OVERLAP_LEN = 20
MEL_CACHE_LEN = 20
SOURCE_CACHE_LEN = MEL_CACHE_LEN * 256

SOURCE_BLOBS = {
    "cosyvoice/utils/class_utils.py": "aab83266a2644b5110aeaa64317c25b61419ba0d",
    "cosyvoice/cli/model.py": "92a15d985dfbca4bb676d3fdc02732be1d07c461",
    "cosyvoice/cli/frontend.py": "6d397cc99be417e98b8d9fc7a1438ec722312276",
    "tt/qwen2_generator.py": "07d3ea4cb18d5a76d35ca0eb70aae52b50ffc308",
    "tt/model_bringup.md": "348bba20a98046286711f319bb1c72494997f67f",
}

FORBIDDEN_HOST_CLAIMS = frozenset(
    {
        "hardware_validated",
        "quality_validated",
        "performance_validated",
        "bounty_accepted",
        "payout_authorized",
    }
)


@dataclass(frozen=True)
class ModeContract:
    name: str
    required_inputs: FrozenSet[str]
    uses_llm: bool
    llm_prompt_text_present: bool
    llm_embedding_present: bool
    handoffs: Tuple[str, ...]


MODE_CONTRACTS: Mapping[str, ModeContract] = {
    "sft": ModeContract(
        "sft",
        frozenset({"text", "speaker_id"}),
        True,
        False,
        True,
        PIPELINE,
    ),
    "zero_shot": ModeContract(
        "zero_shot",
        frozenset({"text", "prompt_text", "prompt_audio"}),
        True,
        True,
        True,
        PIPELINE,
    ),
    "cross_lingual": ModeContract(
        "cross_lingual",
        frozenset({"text", "prompt_audio"}),
        True,
        False,
        True,
        PIPELINE,
    ),
    "instruct": ModeContract(
        "instruct",
        frozenset({"text", "speaker_id", "instruct_text"}),
        True,
        False,
        False,
        PIPELINE,
    ),
    "voice_conversion": ModeContract(
        "voice_conversion",
        frozenset({"source_audio", "prompt_audio"}),
        False,
        False,
        True,
        ("frontend", "flow", "hift"),
    ),
}


HANDOFFS = {
    "llm_to_flow": {
        "value": "semantic_speech_tokens",
        "rank": 2,
        "dtype": "int32",
        "shape": "[batch, semantic_time]",
    },
    "prompt_feat_to_flow": {
        "value": "prompt_speech_feat",
        "rank": 3,
        "dtype": "floating",
        "shape": f"[batch, prompt_time, {MEL_CHANNELS}]",
    },
    "flow_to_hift": {
        "value": "tts_mel",
        "rank": 3,
        "dtype": "floating",
        "shape": f"[batch, {MEL_CHANNELS}, mel_time]",
    },
    "hift_to_output": {
        "value": "tts_speech",
        "rank": 2,
        "dtype": "floating",
        "shape": "[batch, waveform_time]",
    },
}


def validate_mode_fixture(mode: str, supplied_inputs: FrozenSet[str]) -> None:
    """Reject fixtures that do not contain the source-required mode inputs."""
    if mode not in MODE_CONTRACTS:
        raise ValueError(f"unknown mode: {mode}")
    missing = MODE_CONTRACTS[mode].required_inputs - supplied_inputs
    if missing:
        raise ValueError(f"{mode} missing required inputs: {sorted(missing)}")


def validate_claims(claims: Mapping[str, bool]) -> None:
    """Host evidence must never self-promote into device/provider authority."""
    promoted = sorted(key for key in FORBIDDEN_HOST_CLAIMS if claims.get(key) is True)
    if promoted:
        raise ValueError(f"host packet overclaims authority: {promoted}")


def validate_contract() -> None:
    assert PIPELINE == ("frontend", "llm", "flow", "hift")
    assert set(MODE_CONTRACTS) == {
        "sft",
        "zero_shot",
        "cross_lingual",
        "instruct",
        "voice_conversion",
    }
    assert MODE_CONTRACTS["cross_lingual"].llm_prompt_text_present is False
    assert MODE_CONTRACTS["instruct"].llm_embedding_present is False
    assert MODE_CONTRACTS["voice_conversion"].uses_llm is False
    assert MODE_CONTRACTS["voice_conversion"].handoffs == ("frontend", "flow", "hift")
    assert HANDOFFS["prompt_feat_to_flow"]["shape"].endswith(", 80]")
    assert HANDOFFS["flow_to_hift"]["shape"] == "[batch, 80, mel_time]"
    assert TOKEN_OVERLAP_LEN == 20
    assert MEL_CACHE_LEN == 20
    assert SOURCE_CACHE_LEN == 5120
    assert len(TT_METAL_COMMIT) == 40
    assert len(COSYVOICE_COMMIT) == 40
    assert all(len(blob) == 40 for blob in SOURCE_BLOBS.values())


if __name__ == "__main__":
    validate_contract()
    print("HOST_CONTRACT_GREEN")
