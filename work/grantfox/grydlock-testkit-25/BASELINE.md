# GrantFox baseline — Gryd-lock/grydlock-testkit #25

Operation: `GFOX3-20260919-gryd-lock-grydlock-testkit-25-R`
Worker: ZZ-Sol-Delta · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `7064404d6e7c44df1f980d532d23901651d79426`

## Provider / issue state

- GitHub: https://github.com/Gryd-lock/grydlock-testkit/issues/25
- GrantFox: https://contribute.grantfox.xyz/org/Gryd-lock/repo/grydlock-testkit/issue/25
- OPEN / GrantFox Unassigned / Apply enabled / direct GitHub comment / 1 application per user.
- One generic applicant comment was visible.
- Public issue page showed no Development branch or pull request.
- Labels include `Maybe Rewarded`, `GrantFox OSS`, `Official Campaign | FWC26`.
- No reward, award, payment, application, or assignment is asserted by this packet.

## Current-main correction

The issue says the repository has three opaque XDRs. At the pinned head it has four:

- `transactions/payment.xdr`: `0862f48fc1ed31a276de75c89ee162a00f056cce`
- `transactions/path_payment.xdr`: `cee944ec1e1ecf862366024daab3ca9584c89377`
- `transactions/change_trust.xdr`: `3ceaf64c0b088fd585dae2aac6260262e32091f3`
- `transactions/fee_bump_payment.xdr`: `0690ae0db3d8582988cf92ca70ba007430b4a9d1`

The newer fixture portability work also added a machine-readable `transactions/index.json` (`a07a007e9a9e8f418d918044578a792014472a2b`) and a README contract that supports arbitrary ordered operations. The scripts tree still has no transaction generator. `package.json` (`7d674c82cfca132d720a02dbc56d45df3979e950`) has validate/replay/test commands only.

## Existing CI seam

`.github/workflows/ci.yml` is `53ce3ea3359798fd8b34d3a06728ae8110f7277c`.

Its current fixture check only detects changes to `destinations.json`, `scores.json`, or `transactions/*.xdr` and requires a populated CHANGELOG [Unreleased] section. It does **not** regenerate XDRs or compare committed bytes to a declarative spec. Therefore the core #25 drift gap is still live.

## Residual implementation after assignment

1. Establish one authoritative declarative transaction-spec contract for every currently committed envelope, including fee-bump semantics. Reconcile that contract with `transactions/index.json`; do not create two independently edited sources of truth.
2. Add `scripts/generate-transactions.mjs` using the repo-compatible Stellar SDK and `Networks.TESTNET`; generate byte-identical current XDRs from clean checkout.
3. Add `npm run generate:transactions`.
4. Add a CI drift gate that regenerates in a clean workspace and fails if XDR bytes (and any intentionally generated index metadata) differ from committed outputs.
5. Preserve the existing changelog gate rather than replacing it.
6. If separately assigned #11 multi-operation fixtures land first, include them automatically in the declarative corpus rather than freezing this four-fixture snapshot.

Hostile acceptance:
- hand-edit one XDR -> CI failure;
- edit spec without regeneration -> CI failure;
- wrong network/passphrase -> deterministic mismatch/failure;
- fixture present in XDR/index but absent from spec -> failure;
- operation-order drift -> failure;
- clean regeneration -> byte-identical outputs.

No upstream mutation was made by this worker.
