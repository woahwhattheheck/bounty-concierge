# GrantFox source fence — CalloraOrg/Callora-Frontend #986

**Operation:** `GFOX-QUASAR-A3/R-signing-context-trust-boundary`  
**Worker:** ZZ-Solstice-69 · GPT-5.6 Sol  
**Observed:** 2026-09-19  
**Disposition:** PREREQUISITE_SOURCE_MISMATCH / implementation remains maintainer-assignment-gated

## Provider / issue state

- Repository: `CalloraOrg/Callora-Frontend`
- Pinned default-branch source: `cbf382eadb26d863a82694ae36cc85ced20b4d51`
- Issue: #986, `[GrantFox][High] Block signing on wrong network or account`
- GitHub issue: OPEN, unassigned, 5 applicant comments at observation
- Upstream connector: pull=true, push=false
- Open PR search for `#986`: none observed
- Issue explicitly says contributors must wait for maintainer assignment before coding.
- No assignment, reward, wallet, signing, transaction, or payment action is performed by this packet.

## Source correction: there is no real signing boundary on current main

The issue describes a production wallet trust boundary — connected network, expected account/authority, switching, signing and submission. Current `main` does not yet contain that boundary.

### Package/dependency evidence

`package.json` blob `f9ad9483b2c75cfaedb6e34f72b97e2bf7fd798b` includes React/Vite/Vitest dependencies and no Stellar SDK, Freighter integration, wallet kit, signing library, or transaction submission dependency.

Repository searches for `signTransaction`, `Freighter`, `networkPassphrase`, and `publicKey` returned no code hits during this audit.

### Current “Approve Transaction” is a UI simulation

`src/App.tsx` blob `d3917c3dd750ac37fbe95d5aaac09c96360ee2f0` implements:

- `createMockHash()`: creates a random-looking 64-character hash from `Date.now()` + `Math.random()`.
- `handleApproveTransaction()`: snapshots local amount/balance, creates that mock hash, then:
  - immediately enters `approving`;
  - after 1.4 seconds enters `pending` and displays “Transaction submitted to Stellar”;
  - after 3.6 seconds either increments the local in-memory vault balance or marks failure according to the developer-controlled `demoOutcome`.
- no wallet request;
- no transaction construction;
- no signature;
- no network query;
- no public-key lookup;
- no submission RPC/API call.

The UI text says “Your wallet signs a USDC deposit” and “Approve in wallet,” but current source is explicitly demonstrative behavior, not a wallet integration.

### Current account switcher is not a Stellar account switcher

`src/hooks/useAccountContext.tsx` blob `3e039c68ad8077022397641345736c749e1f697c` and `src/state/accountStore.ts` blob `bbc95a1f7cbcf3ef1489e3fd1962f606025ca6f0` model Callora application accounts:

```ts
{ id, label, apiKey, timezone? }
```

with defaults such as `account-1` / `account-2`.

`AccountSwitcher` changes that application account. It has no Stellar public key, signing authority, wallet session, network passphrase, or wallet event source. Treating this object as the issue's “current account” would create a false security boundary.

### Network identity is not modeled

`src/config/constants.ts` blob `c65c8fe731e034a59bd4a52cd2c0a5abf02e2fc4` contains a hard-coded testnet explorer URL:

`https://stellar.expert/explorer/testnet/tx/`

and a human-readable fee string. It does not define a canonical network passphrase, supported-network set, wallet network state, or switching API.

### The current preview cannot satisfy the final-summary contract

`src/components/DepositPreview.tsx` blob `1eaca83c8d7ae9343e70de4ba55b80081d05cbe8` displays:

- vault balance before/after;
- wallet balance before/after;
- amount;
- fee.

It does not receive or render a transaction destination, source public key, asset issuer, actual network identity, or signable transaction envelope. Its test blob `90e2033fc0c389e6a19a06e8e92e2c5e3d514b5a` tests display math/ARIA only.

Therefore issue #986's “destination and amount are shown before signing” cannot truthfully be completed by adding another label to the current simulated preview. The destination must be derived from the transaction that will actually be signed.

## Adjacent dependency: #979

Open issue #979 asks for a deterministic wallet transaction state machine: explicit signing/submission/confirmation/rejection/timeout states, distinct wallet rejection vs network failure, retry/duplicate-click protection.

That is directly adjacent to #986 because a secure preflight guard needs an authoritative attempt lifecycle.

GitHub returned #979 as OPEN/unassigned during this audit. A follow-up PR census for #979 hit GitHub's secondary rate limit (HTTP 403), so this packet does **not** assert that #979 has no implementation carrier. Re-check its PR/assignment state before implementation.

## Required prerequisite contract

Before coding #986, the assigned scope needs a real wallet/transaction seam, either already landed elsewhere or explicitly included by the maintainer.

### 1. WalletSnapshot

A wallet adapter should expose a current snapshot such as:

```ts
type WalletSnapshot =
  | { status: "disconnected" }
  | { status: "connected"; publicKey: string; networkPassphrase: string }
  | { status: "error"; reason: string };
```

The exact API can differ, but public-key and canonical network identity must come from the wallet integration — not the Callora API-account context.

### 2. Immutable SigningIntent

Before review/signing, construct one immutable intent from the actual transaction inputs/envelope:

```ts
type SigningIntent = {
  attemptId: string;
  expectedSource: string;
  networkPassphrase: string;
  destination: string;
  asset: { code: string; issuer?: string };
  amount: string;
  transactionIdentity: string; // envelope/hash/canonical digest as appropriate
};
```

The final human-readable summary must render from this intent (or the exact transaction envelope), not from mutable form fields that can drift after review.

### 3. Final pre-sign guard

Immediately before requesting the signature:

- wallet status must be connected and unambiguous;
- network must be supported;
- current wallet network must equal `SigningIntent.networkPassphrase`;
- current wallet public key must equal `SigningIntent.expectedSource`;
- the intent must still be the active attempt;
- the displayed summary must correspond to that same intent.

Any mismatch fails closed with **zero signing request and zero submission**.

### 4. Switch/reconnect semantics

A network/account switch is not “recover and continue.”

After any requested switch or reconnect:

1. wait for the wallet operation to settle;
2. re-read the wallet snapshot;
3. invalidate the old signing intent;
4. rebuild the transaction/intent from authoritative state if still applicable;
5. show a new summary;
6. require fresh explicit user confirmation.

A rejected, failed, timed-out, ambiguous, or unsupported switch leaves the transaction unsigned. It must never fall through to the old sign/submit continuation.

### 5. TOCTOU reduction

Where the wallet API permits it, pass/lock the expected network/account into the signing request itself in addition to the application-side guard. Application preflight alone cannot close a race if the external wallet can change identity between check and signature.

Submission must only accept the signature/result for the still-current attempt/transaction identity.

## Hostile regression matrix

| Case | Required result |
|---|---|
| unsupported wallet network | block before sign; submit count = 0 |
| supported network + wrong public key | block before sign; submit count = 0 |
| account changes after summary opened | old intent invalid; sign count = 0 |
| network changes after summary opened | old intent invalid; sign count = 0 |
| wallet disconnects/reconnects during review | old intent invalid; require rebuilt summary |
| requested switch succeeds | re-read wallet, rebuild intent, require fresh confirmation; no automatic continuation |
| switch rejected/fails/times out | unsigned; no auto-retry or submit |
| stale async completion from prior attempt arrives | ignored; cannot advance current attempt |
| double-click on approve/sign | at most one active signing attempt |
| wallet rejection | distinct terminal/reviewable outcome from network failure |
| summary values | source/network/destination/asset/amount all derive from same signable intent |
| transaction changes after review | transaction identity mismatch blocks signing/submission |

## Maintainer decision needed before assignment implementation

A correct #986 implementation needs the repository's intended answers to these current-source gaps:

1. Which wallet/provider integration is canonical?
2. Where does the expected Stellar source public key come from, and how does it relate (if at all) to the existing Callora account object?
3. What network(s) are supported, expressed as canonical passphrases/identifiers?
4. Where is the real deposit destination / transaction builder?
5. Is the missing wallet transaction seam part of #986, #979, another issue, or a prerequisite that will land first?

Without those answers, adding a “preflight hook” would either guard a mock timer flow or invent a second wallet architecture.

## Assignment-ready application note

> Re-checking current `main@cbf382eadb26d863a82694ae36cc85ced20b4d51` changes the implementation plan: the existing deposit approval is still a demo flow (`createMockHash` + timers + local balance mutation), `package.json` has no Stellar/Freighter wallet dependency, and the existing `AccountSwitcher` changes Callora API accounts rather than Stellar public keys. I would not wire #986's security checks to those objects.
>
> Once assigned and the intended wallet seam is confirmed, I would bind an immutable signing intent (source/network/destination/asset/amount + transaction identity) to the final summary, re-read the wallet immediately before signing, fail closed on network/account drift, invalidate and rebuild after any switch/reconnect, and prove zero sign/submit on every mismatch or switch failure. I would coordinate the attempt lifecycle with #979 rather than creating a competing state machine.
>
> Please confirm the intended wallet/provider and the authoritative mapping for expected Stellar source account/network, or whether that integration is prerequisite scope for this issue.

## Authority fence

This packet is current-source analysis and an assignment-ready security contract. It does not add a wallet integration, submit/sign transactions, change user accounts, post an upstream application, claim assignment/reward, or mutate upstream source.
