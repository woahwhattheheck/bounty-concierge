# Settlement trust-registry adapter

`concierge.settlement_trust_registry_adapter` is the trusted-side producer for the signed source registry consumed by `concierge.reward_settlement_certifier`. The certifier deliberately does not trust authority labels in settlement JSON; this producer likewise **does not accept settlement JSON at all**.

## Trust boundary

The producer consumes two retained artifacts per source: exact raw capture bytes and an adapter-authenticated receipt. The adapter receipt binds `adapter_id`, `capture_id`, `source_family`, `source_id`, `source_ref`, `observed_at`, the raw byte SHA-256, and raw byte length. Its HMAC is verified with a secret selected by a **code-owned adapter policy**. The registry authority is then derived from that same policy rather than copied from the receipt.

Shipped policies are explicit receipt protocols for repository, official-offer, sponsor, provider, wallet, bank, and operator-capture adapters. Each policy fixes its source family, authority, reference prefix, maximum evidence age, and secret environment-variable name. Adding a new real connector requires source review of this policy table; arbitrary callers cannot add an adapter or remap `PROVIDER` to `BANK` in data.

The final registry HMAC key is read only from the controlled runtime (`REWARD_SETTLEMENT_TRUST_KEY` by default) or a supplied file descriptor. Adapter verification keys are likewise read by code-owned environment-variable names. No key is serialized into the registry, signing receipt, log output, demo, or fixture.

## Adapter receipt schema

An adapter writes strict JSON with exactly these fields:

```text
schema = bounty-concierge/reward-settlement-adapter-receipt/v1
adapter_id
capture_id
source_family
source_id
source_ref
observed_at        # canonical UTC seconds
source_sha256      # SHA-256 of exact retained bytes
source_size        # integer byte length
signature_hmac_sha256
```

The signature covers all fields except `signature_hmac_sha256` using the repository's canonical JSON encoding. A data consumer cannot choose authority, digest, observation time, reference, or signer without invalidating the upstream adapter HMAC or the code-owned policy.

## Build manifest

The build manifest is intentionally weaker and contains only an execution generation plus paths beneath a retained capture root:

```json
{
  "schema": "bounty-concierge/reward-settlement-registry-build/v1",
  "generated_at": "2026-09-17T03:00:00Z",
  "captures": [
    {"receipt_path": "provider.receipt.json", "source_path": "provider.raw"}
  ]
}
```

Both files must be bounded regular non-symlink files. Reads are fenced by inode/device/size/mtime before and after the descriptor read. Relative paths may not escape the capture root. The producer recomputes source digest and length, rejects stale/future evidence, duplicate source IDs, and reminted source fingerprints, then self-checks the generated registry with `reward_settlement_certifier.verify_registry` before publication.

## Controlled execution

Set only the adapter secrets used by the current manifest and the registry signing secret. Do not put secret values in the manifest or command line.

```bash
export REWARD_SETTLEMENT_PROVIDER_ADAPTER_KEY='(runtime secret)'
export REWARD_SETTLEMENT_TRUST_KEY='(runtime secret)'
python -m concierge.settlement_trust_registry_adapter \
  --manifest retained/manifest.json \
  --capture-root retained \
  --registry out/trusted-registry.json \
  --receipt out/trusted-registry.signing.json
```

For runtimes that provision the registry key on a descriptor, use `--registry-key-fd N` instead of an environment secret. The CLI prints only the signing-receipt digest.

Re-run verification against the same retained bytes:

```bash
python -m concierge.settlement_trust_registry_adapter \
  --manifest retained/manifest.json \
  --capture-root retained \
  --registry out/trusted-registry.json \
  --receipt out/trusted-registry.signing.json \
  --verify
```

A successful verification prints `VERIFIED`. The verifier recompiles from retained bytes and adapter receipts, so changed bytes, reminted sources, stale evidence, changed metadata, wrong keys, registry tamper, or receipt tamper fail closed.

## Output and authority ceiling

The registry is exactly `bounty-concierge/reward-settlement-trusted-sources/v1`, the format already consumed by `reward_settlement_certifier`. The separate signing receipt binds manifest bytes, registry body/full digests, each adapter receipt digest, each raw source digest/size, and a hard-false authority object.

This component does **not** send messages, request payout, mutate a provider/wallet/bank, perform accounting, or recognize revenue. A valid registry proves only that the controlled runtime signed source claims that first passed an explicitly configured adapter receipt and exact-byte custody checks. It does not by itself prove a sponsor owes money, a transfer occurred, or any amount is revenue; the downstream certifier still applies its own lifecycle semantics.

## Reference rehearsal

`python examples/settlement_trust_registry_adapter_demo.py` creates synthetic keys in memory, signs synthetic adapter evidence, emits no secret, verifies consumer compatibility, and prints only a content receipt plus the all-false authority object.
