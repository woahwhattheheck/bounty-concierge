# GrantFox baseline — Raegis-RWA/Raegis-sdk #12

Operation: `GFOX-RAEGIS-12-R-PARALLAX-Q7N4-20260919`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `69fff2c7e8c6fe801428fc1bb71d064c5c94949c`

## Provider and collision fence

- GitHub: https://github.com/Raegis-RWA/Raegis-sdk/issues/12
- GrantFox: https://contribute.grantfox.xyz/org/Raegis-RWA/repo/Raegis-sdk/issue/12
- GitHub issue: OPEN / assignees=[] / 0 comments.
- No matching #12 implementation PR surfaced in the focused PR census.
- GrantFox direct read: Apply enabled, one application per user, Direct GitHub comment, Unassigned, 0 comments.
- This worker's browser profile is already known to lack GrantFox credentials. No application was attempted or submitted.

## Current-main correction

Issue #12 is partially absorbed. Current main already contains a network-specific safe diagnostic boundary:

- `src/network/failures.ts` — `872a5409d2f0c87657a6998a1303c659a0ca7380`: inspects raw failures for classification but emits fixed safe messages.
- `src/diagnostics/network.ts` — `a2955a554cb6fee7d2b7f6b5d3fdc730130edc7a`: serialisable diagnostic omits raw error/message/URL/headers/payload.
- `tests/network-failures.test.ts` — `f04545519cc06c43f9c52caf6f6d75e95c1c0929`: proves tokens/private raw messages do not survive diagnostics/JSON.
- `docs/network-failures.md` — `22ba6fa5898c30c4d3e1698efcd12a2ea1a4f460`: documents the safe network boundary.

That work should be reused rather than replaced.

## Material residual

- `src/index.ts` — `b498dcfb2008865b3a98ad0ceaa330ff7a89a547` exports no generic redaction primitive.
- Current diagnostics are network-specific, not a reusable redactor for arbitrary support objects, errors, headers, signatures, XDR/transaction material, or logs.
- `NetworkFailure.cause` retains the original upstream object non-enumerably. JSON is safe, but a general policy for console inspection/support tooling is not defined.
- `README.md` — `4d2bb5a126aebd7e55f5e8316d6a98becffccf7d` and `docs/role-aware-client-factory.md` — `41afd00b7def758853628b0f2083edcac3ad9766` still teach inline `Keypair.fromSecret('S_*_SECRET')` signer examples. The values are fake placeholders, but the pattern conflicts with the issue's safe-quickstart goal.

## Assigned implementation seam

1. Add a pure deterministic redactor for strings and structured values with explicit sensitive-key and Stellar-secret/signature/XDR-like rules. Define depth, cycle, and size caps.
2. Apply it at generic diagnostics/support-report/error-formatting boundaries without mutating source errors. Do not redact public G-addresses merely for being Stellar data.
3. Document the raw `cause` boundary: internal/non-enumerable is acceptable only if any support/log serialisation or inspection uses the redactor.
4. Replace signer quickstarts with environment/secret-store injection; keep fake secrets only in tests.
5. Add hostile tests for S-seeds, auth tokens, private-key fields, signatures, transaction/XDR-shaped values, nested/cyclic structures, huge strings, already-redacted input, and false-positive public keys.
6. Preserve the current safe network diagnostic shape and run `npm run check`.

No upstream source, provider application, assignment, reward, payment, or wallet state was mutated.
