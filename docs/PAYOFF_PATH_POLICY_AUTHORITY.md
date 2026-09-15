# Payoff Path Owner-Policy Host Authority

`payoff-path-work/v3` has an append-only continuity ledger, evidence-bound effort facts, and versioned `BUDGET_POLICY` generations. Those controls prevent reset, omission, replay-with-changed-bytes, receipt-prefix rollback, and work-scope transplant. They do **not**, by themselves, prove that a caller-authored higher budget cap was actually approved by the owner.

Every ordinary/public v3 compilation and verification therefore has a second authority boundary: the exact canonical owner-policy scope must be approved by a credential-owning host through a detached HMAC authorization.

## Why this exists

A self-consistent work document can compute a valid predecessor digest for a new `BUDGET_POLICY` generation. Without an independent authority root, a caller could raise the cap after a genuine `STOP_UNPAID_WORK` and cause the semantic engine to emit `READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW` plus `OWNER_POLICY_SUPERSESSION_REOPENED_AFTER_STOP`.

That is now rejected unless the host authorization matches the exact policy scope.

## Signed scope

The HMAC scope is canonicalized from the normalized v3 document and binds:

- authority purpose: `bounty-payoff-owner-policy-authority/v1`;
- continuity `ledger_id`;
- continuity generation;
- exact previous-receipt SHA-256 anchor;
- immutable work/opportunity scope SHA-256;
- every canonical `BUDGET_POLICY` event in the generation, including event ID, opportunity, cap, occurrence time, evidence ref/SHA, policy generation, and predecessor-policy digest.

Event input order is normalized before the scope digest is computed. Changing a cap, policy evidence, predecessor, work scope, ledger, generation, or prior receipt changes the authorized scope.

The signed payload additionally binds the host-authorized provider, principal SHA-256, and capture time.

## Credential-host boundary

Ordinary/public compile and verify read these values only from the credential host's environment:

- `BOUNTY_PAYOFF_POLICY_HMAC_KEY_HEX` — at least 32 bytes encoded as hex;
- `BOUNTY_PAYOFF_POLICY_AUTHORIZED_PROVIDER` — credential-host/provider identity;
- `BOUNTY_PAYOFF_POLICY_AUTHORIZED_PRINCIPAL_SHA256` — authorized principal digest;
- `BOUNTY_PAYOFF_POLICY_AUTHORITY_CAPTURED_AT_UTC` — canonical UTC capture time;
- `BOUNTY_PAYOFF_POLICY_AUTHORITY_SIGNATURE_SHA256` — HMAC-SHA256 over the exact authority payload.

The key, authorized identity, capture time, and signature are **not** accepted as work-document fields or CLI arguments. The ordinary work submitter must not control the credential-host process, its environment, or arbitrary Python execution inside it. HMAC is a process trust boundary: code execution inside the credential host can read the verification key and is therefore trusted by definition.

The normal `payoff_path_gate` compatibility surface deliberately exposes no signing helper. A trusted credential-host adapter may compute the documented canonical policy scope and detached signature outside the ordinary gate call. The repository's private test fixture signer exists only in the authority implementation module for deterministic tests and is not re-exported through the gate API or CLI.

## Freshness

Current authority is short lived. The capture must not be in the future and must be no more than 300 seconds old when compile/verify runs. Verification reacquires the detached host authority instead of assuming that a packet was current merely because it was previously compiled.

A stale or missing authorization fails closed.

## Historical semantic fixtures

The predecessor v3 state-machine tests need deterministic historical timestamps and intentionally exercise semantics below the new host-authentication boundary. They do this by mocking `verify_current_policy_authority` **inside the unittest process only**.

Production-imported code contains no environment variable, CLI flag, document field, clock argument, or other configuration switch that disables owner-policy authentication. A caller-selected `trusted_as_of` or `trusted_now` value still requires detached host authority. The production CLI continues to expose no `--as-of` override.

## Authority ceiling

A valid owner-policy HMAC authorizes only the owner-review budget policy used by the offline payoff-path gate. It does **not** authorize outreach, bounty/competition submission, proposal delivery, contract/signature action, spend, deployment, payment initiation, payout, refund, cash receipt, accounting treatment, or revenue recognition.

The semantic result remains `OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION`.
