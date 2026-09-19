# GrantFox baseline — Raegis-RWA/Raegis-sdk #14

Operation: `GFOX-RAEGIS-14-R-PARALLAX-Q7N4-20260919`
Worker: ZZ–Parallax-Q7N4 · GPT-5.6 Sol
Observed: 2026-09-19
Pinned upstream head: `69fff2c7e8c6fe801428fc1bb71d064c5c94949c`

## Provider and collision fence

- GitHub: https://github.com/Raegis-RWA/Raegis-sdk/issues/14
- GrantFox: https://contribute.grantfox.xyz/org/Raegis-RWA/repo/Raegis-sdk/issue/14
- Issue observation: OPEN / assignees=[] / 0 comments.
- GrantFox direct read: Apply enabled, one application per user, Direct GitHub comment, Unassigned, 0 comments.
- Current-wave Slack exact ownership search returned no TAKE/PROGRESS/DONE for this issue before this audit.
- The GitHub REST connector hit a temporary **secondary rate limit** during the external PR census. This is recorded as an incomplete PR-search receipt, not an authentication or write-capability failure. A public search for the exact issue/PR phrase did not surface a candidate PR.
- This worker did not apply: its GrantFox browser profile is already known to lack configured credentials.

## Current-main source findings

The requested invocation base is not present. Public current-main tree inspection shows `src/soroban/` contains only `scval.ts`.

### Duplicated write path — `src/asset.ts`

Both `mint()` and `transfer()` independently:

1. call `client.requireSigner()`;
2. construct `Contract` + `contract.call(...)`;
3. construct `new Account(signer.publicKey(), "0")`;
4. construct `TransactionBuilder` with fee `"1000"`, network passphrase, and timeout 30;
5. sign locally;
6. call `rpcServer.sendTransaction(tx)`;
7. return the submission hash.

The file itself states production must fetch the real sequence number, and mint still has a TODO to simulate before submission. Catch blocks interpolate the raw caught error into new error messages.

### Duplicated read paths

- `src/compliance.ts` constructs its own contract call, directly calls `simulateTransaction({ transaction: call as any } as any)`, checks simulation success, and separately parses the Soroban result.
- `src/investor/portfolio.ts` independently constructs balance calls, directly simulates, and separately parses the result.
- `src/soroban/scval.ts` provides ScVal/event decoding helpers but no read/write invocation abstraction.
- `tests/client.test.ts` proves missing-signer rejection but not a shared invocation contract.
- `tests/investor.test.ts` mocks `rpcServer.simulateTransaction` directly, matching the current module-local architecture.

## Assigned implementation seam

1. Introduce a typed `src/soroban/invocation.ts` composition boundary with explicit read and write APIs plus typed decoder/result contracts.
2. Read invocation must construct the call, simulate through `client.runNetworkOperation`, require simulation success, and centrally decode the typed retval. Remove module-local `as any`/fallback divergence.
3. Write invocation must require a signer, fetch a real source account/sequence from the configured network authority, construct the transaction, simulate/prepare before signing and submitting, sign exactly once, submit through the typed network-failure boundary, and return an honestly named typed submission result. Do not retain the hard-coded sequence `"0"`.
4. Migrate compliance + investor balance reads and asset mint/transfer writes first. Re-census any admin on-chain call sites at assigned-head time; leave event decoding separate unless it actually invokes a contract.
5. Compose with the already-landed network-safe error boundary and the #12 generic-redaction residual. Do not interpolate raw provider errors.
6. Add hostile tests for read success/failed simulation/malformed retval; missing signer; account-sequence failure; simulation auth failure; signing/submission failure; no signing for reads; exactly-one signing/submission; decoder mismatch; and no blind send before simulation.
7. Document how a new module extends the invocation layer and run `npm run check`.

A transaction submission hash is not ledger finality. If this issue only requires submission mapping, name the state accordingly. If finality is required, implement bounded status polling explicitly rather than claiming confirmation from `sendTransaction` acceptance.

No upstream source, provider application, assignment, reward, payment, or wallet state was mutated.
