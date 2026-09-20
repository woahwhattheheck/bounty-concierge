"""Dependency-free host semantics oracle for Tenstorrent tt-metal #49307.

This is deliberately not a model implementation and makes no hardware,
performance, or PCC claim.  It freezes the Command-R semantic seams that are
easy to accidentally inherit from Llama-family defaults during a TTNN port.
"""

from __future__ import annotations

import json
import math
from typing import Iterable, List, Optional, Sequence, Tuple


Vector = Sequence[float]


def _vector(values: Iterable[float], name: str) -> List[float]:
    result = [float(value) for value in values]
    if not result:
        raise ValueError(f"{name} must be non-empty")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} must contain only finite values")
    return result


def _weight(weight: Optional[Vector], size: int) -> List[float]:
    if weight is None:
        return [1.0] * size
    result = _vector(weight, "weight")
    if len(result) != size:
        raise ValueError(f"weight length {len(result)} does not match vector length {size}")
    return result


def cohere_layer_norm(values: Vector, weight: Optional[Vector] = None, eps: float = 1e-5) -> List[float]:
    """Mean-centered LayerNorm used by current Hugging Face Cohere Q/K norm."""
    x = _vector(values, "values")
    w = _weight(weight, len(x))
    if not math.isfinite(eps) or eps <= 0:
        raise ValueError("eps must be finite and > 0")
    mean = sum(x) / len(x)
    variance = sum((value - mean) ** 2 for value in x) / len(x)
    inv_std = 1.0 / math.sqrt(variance + eps)
    return [(value - mean) * inv_std * gamma for value, gamma in zip(x, w)]


def rms_norm(values: Vector, weight: Optional[Vector] = None, eps: float = 1e-5) -> List[float]:
    """RMSNorm discriminator matching the semantic family tt-transformers uses today."""
    x = _vector(values, "values")
    w = _weight(weight, len(x))
    if not math.isfinite(eps) or eps <= 0:
        raise ValueError("eps must be finite and > 0")
    mean_square = sum(value * value for value in x) / len(x)
    inv_rms = 1.0 / math.sqrt(mean_square + eps)
    return [value * inv_rms * gamma for value, gamma in zip(x, w)]


def rotate_half_cohere(values: Vector) -> List[float]:
    """Cohere even/odd pair rotation: [x0,x1,x2,x3] -> [-x1,x0,-x3,x2]."""
    x = _vector(values, "values")
    if len(x) % 2:
        raise ValueError("Cohere rotary vectors must have even width")
    output: List[float] = []
    for index in range(0, len(x), 2):
        output.extend((-x[index + 1], x[index]))
    return output


def rotate_half_split_half(values: Vector) -> List[float]:
    """Split-half Llama-style discriminator: [a,b,c,d] -> [-c,-d,a,b]."""
    x = _vector(values, "values")
    if len(x) % 2:
        raise ValueError("split-half rotary vectors must have even width")
    half = len(x) // 2
    return [-value for value in x[half:]] + x[:half]


def apply_rope_pairwise(values: Vector, cos: Vector, sin: Vector) -> List[float]:
    """Apply the current Cohere reference's pairwise rotary equation."""
    x = _vector(values, "values")
    c = _vector(cos, "cos")
    s = _vector(sin, "sin")
    if not (len(x) == len(c) == len(s)):
        raise ValueError("values, cos, and sin must have identical lengths")
    rotated = rotate_half_cohere(x)
    return [value * cv + rv * sv for value, rv, cv, sv in zip(x, rotated, c, s)]


def repeat_kv_heads(kv_heads: Sequence[Vector], num_q_heads: int) -> List[List[float]]:
    """Expand KV heads to Q-head alignment using Hugging Face repeat_kv ordering."""
    if not isinstance(num_q_heads, int) or num_q_heads <= 0:
        raise ValueError("num_q_heads must be a positive integer")
    if not kv_heads:
        raise ValueError("kv_heads must be non-empty")
    normalized = [_vector(head, "kv_head") for head in kv_heads]
    width = len(normalized[0])
    if any(len(head) != width for head in normalized):
        raise ValueError("all KV heads must have the same width")
    num_kv_heads = len(normalized)
    if num_q_heads % num_kv_heads:
        raise ValueError("num_q_heads must be divisible by num_kv_heads")
    repeats = num_q_heads // num_kv_heads
    return [head[:] for head in normalized for _ in range(repeats)]


def silu(value: float) -> float:
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("SiLU input must be finite")
    if x >= 0:
        return x / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return x * exp_x / (1.0 + exp_x)


def swiglu(gate: Vector, up: Vector) -> List[float]:
    """Reference gate used by TT MLP: SiLU(gate_proj) * up_proj."""
    gate_values = _vector(gate, "gate")
    up_values = _vector(up, "up")
    if len(gate_values) != len(up_values):
        raise ValueError("gate and up must have identical lengths")
    return [silu(gate_value) * up_value for gate_value, up_value in zip(gate_values, up_values)]


def scale_logits(logits: Vector, logit_scale: float) -> List[float]:
    """Explicit post-LM-head scaling required by the Cohere causal-LM reference."""
    values = _vector(logits, "logits")
    scale = float(logit_scale)
    if not math.isfinite(scale):
        raise ValueError("logit_scale must be finite")
    return [value * scale for value in values]


def command_r_contract(
    hidden_size: int = 8192,
    intermediate_size: int = 22528,
    num_layers: int = 40,
    num_q_heads: int = 64,
    num_kv_heads: int = 8,
) -> dict:
    """Return structural invariants advertised by tt-metal #49307."""
    ints = {
        "hidden_size": hidden_size,
        "intermediate_size": intermediate_size,
        "num_layers": num_layers,
        "num_q_heads": num_q_heads,
        "num_kv_heads": num_kv_heads,
    }
    if any(not isinstance(value, int) or value <= 0 for value in ints.values()):
        raise ValueError("all structural dimensions must be positive integers")
    if hidden_size % num_q_heads:
        raise ValueError("hidden_size must be divisible by num_q_heads")
    if num_q_heads % num_kv_heads:
        raise ValueError("num_q_heads must be divisible by num_kv_heads")
    head_dim = hidden_size // num_q_heads
    return {
        **ints,
        "head_dim": head_dim,
        "q_per_kv_group": num_q_heads // num_kv_heads,
        "attention_scale": head_dim ** -0.5,
    }


def max_abs_diff(left: Vector, right: Vector) -> float:
    a = _vector(left, "left")
    b = _vector(right, "right")
    if len(a) != len(b):
        raise ValueError("left and right must have identical lengths")
    return max(abs(x - y) for x, y in zip(a, b))


def fixture_report() -> dict:
    """Deterministic hostile vectors suitable for CI and review receipts."""
    hostile = [1.0, 2.0, 3.0, 4.0]
    ln = cohere_layer_norm(hostile)
    rms = rms_norm(hostile)
    cos = [0.0] * len(hostile)
    sin = [1.0] * len(hostile)
    ln_then_rope = apply_rope_pairwise(ln, cos, sin)
    rope_then_ln = cohere_layer_norm(apply_rope_pairwise(hostile, cos, sin))
    kv = [[float(index), float(index) + 0.25] for index in range(8)]
    expanded = repeat_kv_heads(kv, 64)
    report = {
        "contract": command_r_contract(),
        "qk_norm": {
            "input": hostile,
            "cohere_layer_norm": ln,
            "rms_norm_discriminator": rms,
            "max_abs_diff": max_abs_diff(ln, rms),
        },
        "rope": {
            "cohere_pairwise_rotate": rotate_half_cohere(hostile),
            "split_half_discriminator": rotate_half_split_half(hostile),
            "norm_then_rope": ln_then_rope,
            "rope_then_norm": rope_then_ln,
            "order_max_abs_diff": max_abs_diff(ln_then_rope, rope_then_ln),
        },
        "gqa": {
            "source_kv_heads": len(kv),
            "expanded_q_aligned_heads": len(expanded),
            "first_group": expanded[:8],
            "last_group": expanded[-8:],
        },
        "swiglu": swiglu([-4.0, 0.0, 4.0], [2.0, 3.0, 5.0]),
        "scaled_logits_example": scale_logits([-2.0, 0.5, 3.0], 0.25),
    }
    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


if __name__ == "__main__":
    print(json.dumps(fixture_report(), indent=2, sort_keys=True, allow_nan=False))
