# Microsoft Agent Framework Hyperlight allow-list scope widening

Status: **source-confirmed bounty candidate; private-report prep only**  
Owner: **Z-CeriumTurnpike-2026-S4N9 (`ZCT-S4N9`) / GPT-5.6 Sol**  
Operation: `MSRC-MAF-HYPERLIGHT-ALLOWLIST-WIDENING-ZCTS4N9-20260914`

## Executive summary

At `microsoft/agent-framework@61d6c2c23db5d435eaa8c70fa8e5d8770b70720d`, the production Python Hyperlight wrapper normalizes an explicit allowed URL to only its authority/host before registering network capability with Hyperlight. Hyperlight Sandbox 0.6.0 rejects the resulting schemeless target, after which Agent Framework retries by registering both HTTP and HTTPS forms at the host root.

For an application configuration such as `https://api.example.com/v1`, this transforms a narrow capability into host-root permissions for both schemes. Hyperlight Sandbox 0.6.0 itself has stricter scheme and path-prefix enforcement, so the widening occurs in Agent Framework before the permission reaches the sandbox.

This carrier contains only a deterministic, offline source-model reproducer against `example.com`. It performs **no network I/O**, probes no Microsoft service, uses no credentials or customer data, and is not an upstream/public disclosure submission.

## Pinned evidence

- Agent Framework source SHA: `61d6c2c23db5d435eaa8c70fa8e5d8770b70720d`
- Agent Framework file: `python/packages/hyperlight/agent_framework_hyperlight/_execute_code_tool.py`
- Agent Framework dependency declaration: `python/packages/hyperlight/pyproject.toml` pins `hyperlight-sandbox>=0.6.0,<0.7`.
- Hyperlight Sandbox 0.6.0 release commit: `74318c79179faf2eeca6929d01b276cd77e96fe3` (`Bump hyperlight sandbox to 0.6.0 (#201)`).
- Hyperlight 0.6.0 file: `src/hyperlight_sandbox/src/network.rs` parses an allowed target as a URL and `is_allowed()` checks exact host, scheme, port and path prefix before methods.

## Current transformation

The current Agent Framework helper performs the equivalent of:

```text
configured: https://api.example.com/v1
_normalize_domain(...) -> api.example.com
strict Hyperlight registration rejects schemeless URL
retry targets -> http://api.example.com + https://api.example.com
```

The offline reproducer then verifies two predecessor-killing cases:

```text
https://api.example.com/admin       rejected by the original /v1 permission, allowed after wrapper widening
http://api.example.com/v1/items     rejected by the original https permission, allowed after wrapper widening
```

## Security boundary / impact

This is a capability-integrity failure in a sandbox egress control. An application that intentionally grants the guest only one HTTPS path subtree can receive a broader runtime policy than it configured. Depending on the allowed host, that can expose unrelated same-host endpoints and permit cleartext HTTP where the application specified HTTPS.

No severity or payout is claimed here. MSRC eligibility/severity should be decided against the program's current rules and the concrete application threat model.

## Remediation direction

Preserve explicit URL semantics end to end:

1. If a configured target includes `http://` or `https://`, retain its scheme, authority/port and path when normalizing and registering it.
2. Treat a bare host/domain as a separate compatibility shorthand. If both schemes are intentionally supported for that shorthand, expansion should happen only for the bare-host case and be documented as such.
3. Reject ambiguous URL components that are not part of the permission contract (for example userinfo, query or fragment) rather than silently discarding them.
4. Add a regression proving `https://api.example.com/v1` does not register as host-root and cannot authorize either `https://api.example.com/admin` or `http://api.example.com/v1/...`.
5. Keep method restrictions unchanged.

## Local proof

```bash
python deliverables/msrc-maf-hyperlight-allowlist-widening/reproduce.py
```

Expected first line:

```text
REPRODUCED: explicit URL capability is broadened by wrapper normalization
```

The script is a source-model regression only and intentionally performs no network request.

## Disclosure handling

Do not open a public upstream issue or PR containing this report before the authorized MSRC reporting path is chosen. Do not probe live Microsoft services. A private report should include the two pinned upstream SHAs, the deterministic reproducer, the narrow capability expectation, the widened registered targets, and the remediation/test direction above.
