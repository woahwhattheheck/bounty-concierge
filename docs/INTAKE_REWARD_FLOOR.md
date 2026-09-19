# Intake reward floor

`concierge.intake_reward_floor` is the fleet's first economics prefilter. It applies the owner's hard USD queue rule **before** the deeper paid-work/account/claim gates.

## Policy

The checked-in policy is `policies/intake_reward_floor_v1.json`:

- **ACTIVE** — exact verified USD reward **>= $50**
- **PILE** — exact verified USD reward **$10–49.99**; save in `#bounty-pile-10-49`, do not implement while >=$50 work exists
- **IGNORE** — exact verified USD reward **< $10**
- **HOLD** — no exact verified USD reward; this includes discretionary labels such as `Maybe Rewarded` when no concrete amount is published

The router does not perform FX. A native-currency bounty can be recorded in `native_reward_note`, but without an authoritative USD reward it remains `HOLD_UNCONFIRMED`.

The router also does not aggregate small bounties into an executable batch. Thirty $25 items remain thirty `PILE_10_49` items. Batching may be useful for later planning, but it does not override this owner floor.

## Input

```json
{
  "schema": "intake-reward-floor/v1",
  "as_of": "2026-09-19T23:31:00Z",
  "policy": {
    "schema": "intake-reward-floor-policy/v1",
    "active_floor_usd": "50",
    "pile_floor_usd": "10"
  },
  "candidates": [
    {
      "work_id": "ultimateai-14",
      "canonical_source_url": "https://github.com/iyeanur6-cyber/ultimate-ai-platform/issues/14",
      "reward_state": "KNOWN_USD",
      "reward_usd": "250",
      "reward_evidence_url": "https://github.com/iyeanur6-cyber/ultimate-ai-platform/issues/14",
      "observed_at": "2026-09-19T23:30:00Z"
    },
    {
      "work_id": "grantfox-unpriced",
      "canonical_source_url": "https://github.com/example/repo/issues/1",
      "reward_state": "UNKNOWN",
      "reward_evidence_url": "https://github.com/example/repo/issues/1",
      "observed_at": "2026-09-19T23:30:00Z",
      "native_reward_note": "Maybe Rewarded; no exact amount published"
    }
  ]
}
```

Money must be JSON strings or integers, never floating-point numbers. Known USD rewards require `reward_usd`; unknown rewards must not assert one.

## CLI

```bash
python -m concierge.intake_reward_floor examples/intake_reward_floor.example.json
python -m concierge.intake_reward_floor examples/intake_reward_floor.example.json --json
```

The compact output reports queue counts and a SHA-256 receipt. The full JSON output is deterministic and tamper-evident.

## Authority boundary

`ACTIVE_FLOOR_MET` means only **eligible to proceed to the normal paid-work qualification pipeline**. It does not prove that the bounty is available, assigned, accepted, payable, earned, settled, or cash. It does not authorize an external claim, sponsor contact, submission, merge, payment action, or revenue recognition.
