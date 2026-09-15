# Outbound single-writer guard

> **Production authority note:** `OutboundSingleWriter` is the durable **local** state machine only. Since bounty-concierge PR #176, a revenue-bearing cross-seat send must prove current-worker possession of the canonical Commons `outbound-send-lease/v2` lease through `concierge.outbound_capability_gate.prepare_send_with_capability_lease(...)` before local `SENDING` is armed. The legacy Slack `CLAIM` election and a direct call to `OutboundSingleWriter.prepare_send()` are **not** sufficient production authority for an external send.

Revenue outreach must not become a race between agents. This control gives an outbound mutation one stable SHA-256 operation key and one durable local send state, but it does not itself establish swarm-wide ownership.

It does **not** send email, Slack messages, GitHub comments, payment requests, or customer messages. It is an idempotency/crash-recovery primitive used underneath the global capability gate.

## Authority layers

A filesystem lock is not a swarm lock. Separate cloud sessions do not share a local disk, so local mutual exclusion alone cannot stop two seats from emailing the same lead seconds apart.

Production sends therefore require distinct layers:

1. **Independent outbound preflight.** Owner/content approval, exact route and identity, DNR/cooldown and dedupe checks, buyer/offer scope, and provider-specific requirements must already be frozen.
2. **Canonical global possession proof.** Commons `tools/outbound_send_guard/capability_lease.py` owns `outbound-send-lease/v2` acquisition in `woahwhattheheck/commons`. The current worker must privately retain the matching 256-bit capability and prove it against fresh live GitHub ref + annotated-tag state.
3. **Local send state machine.** `OutboundSingleWriter` protects one local state directory against duplicate local transitions and makes ambiguous sends non-expiring until reconciled.

The historical `evaluate_shared_claim(...)` Slack election remains useful for advisory coordination and collision telemetry only. Slack search/index snapshots are not provider-linearizable and MUST NOT be the sole authority for a revenue-bearing send.

## Stable identity

Use the same values across every peer:

- `provider`: e.g. `gmail`, `slack`, `github`;
- `destination`: the canonical recipient/channel/repository destination;
- `thread`: an existing provider thread id, or a stable lead/customer id for a new thread (never a per-seat random UUID);
- `operation`: stable business action, e.g. `initial-outreach`, `merged-bounty-collection`, or `proposal-followup-1`.

`outbound_operation_key()` hashes the normalized identity. The operation key is one input to the v2 global claim preflight; it is not itself send authority.

## Production pre-send protocol

Assume the derived local operation key is `K`.

1. Complete and freeze the independent outbound preflight. Do not treat a local HELD lease, a Slack claim, or a public Commons receipt as owner/content/route approval.
2. Build the canonical Commons v2 claim with `build_commons_v2_claim(...)`. Its preflight digest binds `K` plus the already-frozen outbound-preflight SHA-256.
3. Acquire `outbound-send-lease/v2` in `woahwhattheheck/commons` and retain the raw capability privately. Never put the capability in Slack, GitHub issues/PR text, provider metadata, argv, logs, or proof receipts.
4. Acquire the local `OutboundSingleWriter` HELD lease for the exact outbound identity.
5. Call `prepare_send_with_capability_lease(...)` from `concierge.outbound_capability_gate`, passing the public v2 claim/receipt, private capability, exact local operation/preflight generation, local HELD lease id, and local owner.
6. The capability gate checks the private commitment **before provider I/O**, re-reads the exact live Commons lease ref and annotated tag, validates tag name/metadata/tagger/anchor and local bindings, then and only then advances the local state to `SENDING`.
7. A successful capability-gate proof still returns `external_send_authorized: false`. The caller must independently satisfy every owner/content/DNR/route/provider gate before performing exactly one external mutation.
8. On provider success, call `finalize_sent()` with the provider receipt id and retain/post only the safe receipt evidence required by the owning workflow.
9. If the process enters `SENDING` but no receipt is recorded, **do not retry**. Provider-specific authoritative reconciliation is required; uncertainty remains blocked.

A local HELD lease may be released before `SENDING` with `abort_held()`. Time passing is never evidence that a global claim or an ambiguous external effect is safe to retry.

## Advisory Slack election

`evaluate_shared_claim(...)` and the `elect` CLI are retained for historical compatibility, collision telemetry, and operator diagnostics. They accept caller-supplied normalized events and a completeness assertion; therefore they are not a provider-owned global mutex and MUST NOT authorize a production external send.

Do not use an `AUTHORIZED` result from `evaluate_shared_claim(...)` as a substitute for the Commons v2 capability lease.

## CLI and local API

The module remains directly executable for stable-key calculation, advisory election, and local-state inspection:

```bash
python -m concierge.outbound_singlewriter key \
  --provider gmail \
  --destination buyer@example.com \
  --thread lead:buyer-123 \
  --operation initial-outreach
```

For advisory election diagnostics, save a normalized provider snapshot and run:

```bash
python -m concierge.outbound_singlewriter elect \
  --key "$K" \
  --owner Z-MengerKiln-2216-T2F9 \
  --claim-event-id 1789352960.235499 \
  --events-json /tmp/outbound-events.json \
  --snapshot-complete
```

Exit code `0` means only that the supplied advisory snapshot elected this owner. It does **not** mean a production send is globally authorized.

`OutboundSingleWriter.acquire()`, `prepare_send()`, `finalize_sent()`, `abort_held()`, and `reconcile_not_sent()` remain the low-level local state-machine API for compatibility, tests, and the capability-gate implementation. **External provider senders must not call `prepare_send()` directly.** Production code must enter `SENDING` through `prepare_send_with_capability_lease(...)` so Commons v2 possession is verified first.

## Safety boundaries

- This is idempotency/custody evidence, not proof a customer owes money or a sponsor accepted a claim.
- Never put credentials, message bodies, API tokens, private data, wallet keys, or the raw v2 capability in operation fields, evidence references, coordination events, or logs.
- Destination normalization is intentionally conservative. Callers must agree on one canonical destination string; the guard will not guess that two aliases are equivalent.
- Local state integrity is SHA-256 sealed and writes are `fsync` + atomic replace, but local state does not establish cross-seat ownership.
- `SENDING` does not auto-expire. Time passing is not evidence that an external mutation failed.
- Slack `CLAIM`/`RELEASE`/`SENT` history is advisory coordination, not the production mutex.
- Public v2 lease receipts/tags are not possession proof without the matching private capability.
- The capability lease is still only a mutex prerequisite: it does not authenticate the buyer, approve content or price, override DNR/cooldown, or authorize the provider mutation by itself.
