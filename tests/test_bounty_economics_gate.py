# SPDX-License-Identifier: MIT
import json
from copy import deepcopy

import pytest

from concierge.bounty_economics_gate import (
    BountyEconomicsInputError,
    compile_economics_gate,
    verify_economics_receipt,
)


SHA = "a" * 64


def request(
    *,
    kind="FIXED",
    verified=True,
    min_cents=5_000,
    max_cents=5_000,
    native_currency=None,
    native_amount=None,
):
    return {
        "schema": "bounty-economics-gate/v1",
        "opportunity_id": "demo/repo#1",
        "program": "Demo bounty",
        "reward": {
            "kind": kind,
            "source_ref": "github:demo/repo#1",
            "source_sha256": SHA,
            "usd_basis_verified": verified,
            "min_usd_cents": min_cents,
            "max_usd_cents": max_cents,
            "native_currency": native_currency,
            "native_amount": native_amount,
        },
    }


@pytest.mark.parametrize(
    ("cents", "expected", "channel"),
    [
        (5_000, "ACTIVE_50_PLUS", "#bug-bounty"),
        (4_999, "PILE_10_49", "#bounty-pile-10-49"),
        (1_000, "PILE_10_49", "#bounty-pile-10-49"),
        (999, "PRUNE_BELOW_10", None),
        (0, "PRUNE_BELOW_10", None),
    ],
)
def test_fixed_boundaries(cents, expected, channel):
    receipt = compile_economics_gate(
        request(min_cents=cents, max_cents=cents)
    )
    assert receipt["disposition"] == expected
    assert receipt["routing"]["channel"] == channel
    assert verify_economics_receipt(receipt)


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        (5_000, 10_000, "ACTIVE_50_PLUS"),
        (1_000, 4_999, "PILE_10_49"),
        (0, 999, "PRUNE_BELOW_10"),
        (4_999, 5_000, "HOLD_RANGE_CROSSES_BUCKET"),
        (999, 1_000, "HOLD_RANGE_CROSSES_BUCKET"),
        (500, 8_000, "HOLD_RANGE_CROSSES_BUCKET"),
    ],
)
def test_range_must_stay_inside_one_scheduling_bucket(minimum, maximum, expected):
    receipt = compile_economics_gate(
        request(kind="RANGE", min_cents=minimum, max_cents=maximum)
    )
    assert receipt["disposition"] == expected
    if expected.startswith("HOLD_"):
        assert receipt["routing"]["allowed_actions"] == ["VERIFY_ECONOMICS_ONLY"]


def test_unpriced_and_discretionary_are_not_active():
    for kind in ("UNPRICED", "DISCRETIONARY"):
        receipt = compile_economics_gate(
            request(
                kind=kind,
                verified=False,
                min_cents=None,
                max_cents=None,
            )
        )
        assert receipt["disposition"] == "HOLD_UNPRICED"
        assert receipt["routing"]["channel"] is None


def test_token_units_do_not_become_usd_by_inference():
    receipt = compile_economics_gate(
        request(
            kind="TOKEN_ONLY",
            verified=False,
            min_cents=None,
            max_cents=None,
            native_currency="RTC",
            native_amount="500",
        )
    )
    assert receipt["disposition"] == "HOLD_TOKEN_ONLY"
    assert receipt["reason_codes"] == ["NO_VERIFIED_USD_CASH_BASIS"]
    assert receipt["evidence"]["reward"]["native_amount"] == "500"


def test_unverified_large_cash_amount_is_hold_not_active():
    receipt = compile_economics_gate(
        request(verified=False, min_cents=100_000, max_cents=100_000)
    )
    assert receipt["disposition"] == "HOLD_UNVERIFIED_CASH"
    assert receipt["routing"]["allowed_actions"] == ["VERIFY_ECONOMICS_ONLY"]


def test_fixed_reward_requires_one_exact_amount():
    with pytest.raises(BountyEconomicsInputError, match="equal min_usd_cents"):
        compile_economics_gate(request(min_cents=5_000, max_cents=6_000))


def test_range_reward_requires_ordered_bounds():
    with pytest.raises(BountyEconomicsInputError, match="min_usd_cents <= max_usd_cents"):
        compile_economics_gate(
            request(kind="RANGE", min_cents=6_000, max_cents=5_000)
        )


def test_unpriced_cannot_smuggle_verified_usd_amount():
    with pytest.raises(BountyEconomicsInputError, match="must not carry USD"):
        compile_economics_gate(
            request(
                kind="UNPRICED",
                verified=False,
                min_cents=5_000,
                max_cents=5_000,
            )
        )


def test_token_only_requires_native_terms():
    with pytest.raises(BountyEconomicsInputError, match="requires native_currency"):
        compile_economics_gate(
            request(
                kind="TOKEN_ONLY",
                verified=False,
                min_cents=None,
                max_cents=None,
            )
        )


def test_exact_input_schema_rejects_hidden_fields():
    payload = request()
    payload["reward"]["estimated_usd"] = 999_999
    with pytest.raises(BountyEconomicsInputError, match="exact fields"):
        compile_economics_gate(payload)


def test_lone_surrogate_in_source_ref_is_rejected_before_hashing():
    payload = request()
    payload["reward"]["source_ref"] = "bad\ud800source"
    with pytest.raises(BountyEconomicsInputError, match="lone surrogates"):
        compile_economics_gate(payload)


def test_receipt_tamper_cannot_be_relabelled_active():
    receipt = compile_economics_gate(
        request(min_cents=2_000, max_cents=2_000)
    )
    assert receipt["disposition"] == "PILE_10_49"
    tampered = deepcopy(receipt)
    tampered["disposition"] = "ACTIVE_50_PLUS"
    assert not verify_economics_receipt(tampered)


def test_rehashing_a_forged_receipt_still_fails_semantic_recompile():
    receipt = compile_economics_gate(
        request(
            kind="TOKEN_ONLY",
            verified=False,
            min_cents=None,
            max_cents=None,
            native_currency="RTC",
            native_amount="10000",
        )
    )
    forged = deepcopy(receipt)
    forged["disposition"] = "ACTIVE_50_PLUS"
    forged["routing"]["channel"] = "#bug-bounty"
    # Even an attacker who rehashes arbitrary JSON cannot satisfy the verifier,
    # because verification recompiles from the retained reward evidence.
    body = {k: v for k, v in forged.items() if k != "economics_receipt_sha256"}
    import hashlib
    forged["economics_receipt_sha256"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    assert not verify_economics_receipt(forged)
