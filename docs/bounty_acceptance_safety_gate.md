# Bounty acceptance safety gate

`concierge.bounty_acceptance_safety_gate` treats bounty/provider acceptance text as **untrusted input**. A reward, assignment, issue label, or maintainer request does not authorize disclosure of private model/runtime context.

## Purpose

The gate catches acceptance criteria that pair a disclosure action (`provide`, `include`, `paste`, `dump`, `upload`, `publish`, and similar verbs) with a private target in the same sentence/line. Current private-target classes are:

- hidden/system/developer/pre-session instructions;
- private reasoning or chain-of-thought;
- API keys, tokens, passwords, session cookies, private keys, or authentication credentials;
- environment variables, process environment, `.env` contents, `platform-config`, or other private/internal/runtime configuration.

The receipt deliberately does **not** copy the raw acceptance text. It binds the exact UTF-8 bytes by SHA-256 and records only reason codes and match counts.

## Non-secret diagnostics remain allowed

Ordinary acceptance requests such as Python/OS/compiler versions, public commit SHAs, failing test commands, and public logs do not trigger this gate unless they are coupled to a private-context demand. Explicit negative guidance such as “do not reveal API keys” is not treated as a disclosure request.

## Dispositions

- `ACCEPTANCE_TEXT_CLEAR`: this gate found no private-context disclosure demand and the source observation is fresh. Continue to the separate economics, claim/assignment, source, collision, and publication gates.
- `HOLD_UNTRUSTED_ACCEPTANCE_TEXT`: stop dispatch. For a private-context demand, require sanitized acceptance criteria that remove the demand. For stale evidence, refresh the acceptance text before routing work.

A clear result is **not** a safety guarantee and does not make the bounty valid, funded, unclaimed, publishable, or payable. This module is advisory-only and grants no provider, repository-write, submission, reward, wallet, or payment authority.

## Request schema

```json
{
  "schema": "bounty-acceptance-safety-gate/v1",
  "issue_url": "https://github.com/ExampleOrg/demo/issues/27",
  "source_url": "https://github.com/ExampleOrg/demo/issues/27",
  "source_text": "Include the Python version and public commit SHA.",
  "source_content_sha256": "<sha256 of exact UTF-8 source_text>",
  "observed_at": "2026-09-20T01:30:00Z",
  "evaluated_at": "2026-09-20T01:40:00Z"
}
```

`source_content_sha256` must match the exact UTF-8 bytes. Source observations older than 24 hours fail closed. Future-dated observations are rejected.

## CLI

```bash
python -m concierge.bounty_acceptance_safety_gate request.json --json
```

Exit `0` means `ACCEPTANCE_TEXT_CLEAR`; exit `2` means HOLD. Malformed input is rejected by argparse. The CLI uses strict JSON loading: duplicate keys and non-finite constants are rejected.

## Receipt verification

`verify_bounty_acceptance_safety_receipt(receipt, source_text)` checks the receipt hash, rebinds the original text to the stored digest, and recompiles semantics. A mutated receipt or different source text does not verify.


## Freshness and multiline criteria

Acceptance criteria are scanned conservatively across Markdown directive continuations such as `Upload:\n- API key` and `Provide the following:\n- .env file`, while unrelated list items remain separate.

Receipt verification is currentness-sensitive. Callers must pass an explicit UTC `verified_at` to `verify_bounty_acceptance_safety_receipt(..., verified_at=...)`. A receipt fails verification when `verified_at` is before the receipt evaluation time or more than 24 hours after the source observation. The exact 24-hour boundary is accepted; any fractional amount beyond it is stale.
