# Architecture — path-payment route discovery, safety and atomic execution

Target: `StarsForges/StarForge#76`  
Pinned source: `master@c2311c6cfbe25ab841ad6a18be60df0fe4f2fe61`

## 1. Safety invariant

The exact transaction body the user reviews is the transaction body that gets signed.

For path payments, that body binds:
- network passphrase;
- source account;
- sequence;
- fee;
- time bounds;
- memo;
- strict-send/strict-receive operation;
- source/destination assets;
- exact fixed amount;
- exact slippage bound;
- exact intermediate path;
- destination.

Any mutation to one of those inputs after confirmation invalidates the previous confirmation and signatures.

This is the architectural rule that prevents a route refresh, sequence retry, fee retry, or stale quote from becoming an invisible different payment.

## 2. Suggested module boundary

A cohesive production implementation can use:

```
src/payment/
  mod.rs
  domain.rs
  transport.rs
  ranking.rs
  preflight.rs
  xdr.rs
  signing.rs
  plan.rs
  store.rs
  watch.rs
src/commands/payment/
  mod.rs
  output.rs
```

Extract generic transaction-envelope primitives from signer-rotation only when doing so reduces duplication without destabilizing that already-working subsystem.

Avoid putting new path-payment business logic directly into `utils/horizon.rs`.

## 3. Exact domain types

### 3.1 Amount7

```rust
#[derive(Clone, Copy, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct Amount7(i64);
```

Construction:
- accepts positive canonical decimal string;
- maximum seven fractional digits;
- no exponent notation;
- no NaN/infinity;
- checked i128 intermediate;
- final value must fit positive i64;
- reject zero when an operation requires positive amount.

Serialization:
- JSON string, not JSON float;
- canonical decimal form with at most seven fractional digits.

Operations:
- checked add/subtract;
- checked basis-point floor/ceil;
- comparison/ranking using integer representation.

Do not pass route amounts through `f64`.

### 3.2 Asset

```rust
pub enum ClassicAsset {
    Native,
    Credit { code: AssetCode, issuer: String },
}
```

Validation:
- native uses a single canonical representation such as `XLM`;
- credit code 1–12 ASCII alphanumeric;
- issuer parses as the supported Stellar issuer account form;
- code comparison remains case-sensitive according to Stellar asset identity;
- never silently replace issuer or code.

Stable key:
- `native`
- `credit:<CODE>:<ISSUER>`

This stable key is used only for local deterministic sorting/serialization, not as an on-chain representation.

### 3.3 Path length

Classic path payments have a bounded intermediate path. Represent the domain with a bounded vector and reject an overlong Horizon response before transaction construction.

Do not truncate a route.

### 3.4 Memo

```rust
pub enum StellarMemo {
    None,
    Text(String),
    Id(u64),
    Hash([u8; 32]),
    Return([u8; 32]),
}
```

Validate byte length, not Unicode character count, for text.

## 4. Horizon transport boundary

Define a trait independent of CLI rendering:

```rust
pub trait PathQuoteTransport {
    fn strict_send(&self, req: &StrictSendRequest) -> Result<PathResponse>;
    fn strict_receive(&self, req: &StrictReceiveRequest) -> Result<PathResponse>;
}
```

Production adapter reuses the current bounded Horizon agent behavior.

Tests use deterministic fixtures/in-memory transport.

### 4.1 Query construction

Use exact percent-encoding and a typed asset query encoder.

Strict send request binds:
- source asset;
- source amount;
- one or more allowed destination assets.

Strict receive binds:
- one or more allowed source assets;
- destination asset;
- destination amount.

Never concatenate user input into a URL without encoding.

### 4.2 Response parsing

Parse Horizon records into a transport DTO, then validate into domain routes.

For each record validate:
- source asset;
- destination asset;
- exact amount strings;
- path assets;
- required fields.

Partial response policy:
- malformed records are not silently treated as good;
- retain a deterministic warning/count of rejected records;
- if at least one valid record remains, the response may produce routes plus diagnostics;
- if no usable record remains, return a typed no-usable-route/partial-response error.

This allows robust operation when one record is malformed without hiding transport corruption.

## 5. Route quote

```rust
pub enum PaymentMode {
    StrictSend,
    StrictReceive,
}

pub struct RouteQuoteV1 {
    pub schema_version: u32,
    pub quote_id: String,
    pub mode: PaymentMode,
    pub network_passphrase_sha256: String,
    pub source_asset: ClassicAsset,
    pub destination_asset: ClassicAsset,
    pub requested_amount: Amount7,
    pub source_amount: Amount7,
    pub destination_amount: Amount7,
    pub path: Vec<ClassicAsset>,
    pub quoted_at: DateTime<Utc>,
    pub accept_until: DateTime<Utc>,
    pub horizon_observation: HorizonObservation,
    pub warnings: Vec<RouteWarning>,
    pub policy_digest_sha256: String,
}
```

`quote_id` should be a SHA-256 digest of canonical quote identity fields. Do not use a random ID as the only tamper-binding mechanism.

The quote acceptance TTL is local StarForge policy. It does not claim the market route remains executable until that timestamp.

## 6. Deterministic ranking

### Strict send

Sort by:
1. valid under hard asset policy;
2. destination amount descending;
3. risk/policy penalty ascending;
4. path length ascending;
5. stable path key ascending.

### Strict receive

Sort by:
1. valid under hard asset policy;
2. source amount ascending;
3. risk/policy penalty ascending;
4. path length ascending;
5. stable path key ascending.

No floating score.

### 6.1 Asset policy

Example policy fields:
- issuer allowlist;
- issuer denylist;
- asset allowlist/denylist;
- maximum hops;
- whether native intermediates are allowed;
- whether an unknown issuer is reject or warn.

The policy itself gets canonical serialization and SHA-256 digest so a persisted quote can prove which policy selected it.

### 6.2 Warnings

Evidence-backed warnings can include:
- route has N hops;
- route contains asset not in preferred list;
- source/destination trustline currently unavailable;
- quote is near local freshness deadline;
- Horizon returned rejected/malformed sibling records.

Do not claim "low liquidity" without a defined observation. If the implementation later probes amount sensitivity/orderbook depth, define that algorithm and evidence explicitly.

## 7. Slippage contract

Represent tolerance as integer basis points:

```rust
pub struct SlippageBps(u32);
```

Use checked i128 intermediates.

Strict send:
```
dest_min = floor(quoted_destination * (10_000 - bps) / 10_000)
```

Strict receive:
```
send_max = ceil(quoted_source * (10_000 + bps) / 10_000)
```

Reject a tolerance outside the configured safe policy before transaction construction.

The preflight summary shows both quoted and bound amounts.

## 8. Quote freshness and refresh

State:

```rust
pub enum QuoteFreshness {
    Fresh,
    Expired,
    NetworkChanged,
    PolicyChanged,
}
```

Before building:
- network passphrase hash must match current network;
- policy digest must match;
- current time must be before `accept_until`.

Before signing:
- repeat freshness;
- verify the built plan references the same quote digest.

Before submitting:
- verify envelope body digest equals the confirmed plan;
- verify time bounds;
- verify quote acceptance policy;
- verify source sequence assumptions remain applicable if the workflow requires a fresh account observation.

### Refresh rule

If a quote is expired:
- refresh;
- rank again;
- compare the new selected route and bounds to the old plan;
- render a fresh confirmation summary;
- require new signing.

Never automatically refresh and submit under the earlier confirmation.

## 9. Destination preflight

```rust
pub struct DestinationPreflight {
    pub account_exists: bool,
    pub destination_asset: ClassicAsset,
    pub trustline: Option<TrustlineEvidence>,
    pub receiving_authorized: Option<bool>,
    pub observed_at: DateTime<Utc>,
}
```

Rules:
- path-payment destination account must exist;
- credit destination requires exact trustline match;
- if Horizon exposes authorization state, enforce it;
- unknown authorization state is not equivalent to authorized;
- native destination needs no trustline but still needs the account;
- destination is never silently changed.

Include the observed destination account ID and asset in confirmation output.

## 10. Source preflight

Capture:
- source account ID;
- network;
- sequence;
- fee basis;
- relevant source balance/trustline;
- quote freshness;
- route policy;
- destination preflight;
- memo;
- exact slippage bound.

For strict send, ensure source has plausible available balance for the exact source amount plus fee/reserve policy.

For strict receive, ensure source can cover `send_max`, not just the quoted source amount.

The on-chain operation remains the ultimate atomic bound.

## 11. Real XDR builder

Do not use `build_payment_transaction_xdr()` from the current mock payment helper.

Build with `stellar_xdr::curr` as proven in signer-rotation.

### Strict send operation

Construct the real equivalent of:
- source asset;
- send amount;
- destination;
- destination asset;
- destination minimum;
- exact intermediate path.

### Strict receive operation

Construct:
- source asset;
- send maximum;
- destination;
- destination asset;
- destination amount;
- exact intermediate path.

Transaction:
- source account;
- current expected sequence;
- exact fee;
- finite time bounds no later than accepted execution horizon;
- memo;
- exactly the confirmed path-payment operation unless explicit multi-op behavior is separately designed.

Return:
- unsigned envelope XDR;
- transaction body SHA-256;
- signature payload SHA-256;
- operation summary.

## 12. Signing abstraction

```rust
pub trait PaymentSigner {
    fn public_key(&self) -> Result<String>;
    fn sign(&self, envelope: &UnsignedPaymentEnvelope) -> Result<SignedPaymentEnvelope>;
}
```

Implementations can include:
- local software signer using existing encrypted-wallet resolution and zeroizing secret handling;
- Ledger signer using the real transaction signature payload flow;
- offline/no-signer mode that exports the unsigned bundle.

Trezor:
- do not advertise signing until its Stellar transaction signing path is implemented;
- report an explicit capability error if selected with the current abstraction.

After signing:
- decode the envelope;
- verify transaction body digest is unchanged;
- verify signature(s) cryptographically when possible.

## 13. Offline bundle

```rust
pub struct OfflinePaymentBundleV1 {
    pub schema_version: u32,
    pub network_passphrase_sha256: String,
    pub quote: RouteQuoteV1,
    pub plan: PaymentExecutionPlanV1,
    pub unsigned_envelope_xdr: String,
    pub transaction_body_sha256: String,
    pub created_at: DateTime<Utc>,
}
```

No secret keys.

Write using the existing atomic private-file pattern:
- temp file;
- 0600 Unix permissions;
- sync;
- rename;
- cleanup.

### Inspect

`starforge payment inspect <file>`:
- bounds file size;
- validates schema;
- decodes real XDR;
- recomputes body hash;
- recomputes quote/policy digests;
- compares decoded operation to plan;
- displays freshness;
- displays signatures without exposing secret material;
- exits nonzero on tamper/mismatch/expiry according to the documented command contract.

## 14. Submission

Submission adapter can reuse Horizon POST semantics after the envelope is real.

Before POST:
1. decode envelope;
2. body hash equals plan;
3. network identity matches;
4. time bounds valid;
5. quote freshness policy passes;
6. signatures satisfy selected signing mode.

On:
- `txBAD_SEQ`;
- fee insufficiency;
- route/stale-path operation failure;

do **not** silently rebuild/sign and retry.

Return a typed result such as:
- stale-source-sequence → regenerate plan;
- fee-changed → regenerate/reconfirm if body changes;
- path-failed → refresh quote/reconfirm;
- destination-trustline failure → destination preflight failed.

Atomicity comes from one Stellar transaction with exact path-payment bounds, not from retrying mutated bodies invisibly.

## 15. CLI design

Top-level:

```
starforge payment paths
starforge payment quote
starforge payment send-strict
starforge payment receive-strict
starforge payment inspect
starforge payment watch
```

### paths

Purpose:
- fetch and display valid route candidates;
- no signing.

Inputs:
- source/destination assets;
- amount + mode;
- network;
- asset policy flags/file;
- `--json`.

Output:
- candidates;
- rejected-record diagnostics;
- observed time;
- deterministic ranking metadata.

### quote

Purpose:
- produce one or more ranked quote objects;
- optionally write versioned quote file.

No signing.

### send-strict

Inputs:
- source wallet/account;
- fixed source amount;
- source/destination assets;
- destination;
- slippage;
- memo;
- route selector/policy;
- network;
- `--dry-run`;
- `--yes`;
- `--hardware ledger`;
- `--export-unsigned <path>`;
- `--json`.

Flow:
discover → rank → destination/source preflight → exact bound → real envelope → summary → confirm → sign/export → submit.

### receive-strict

Same shape, with fixed destination amount and exact `send_max`.

### inspect

Offline, deterministic, no network required unless an explicit `--refresh` is requested.

### watch

Repeated quote observation:
- bounded minimum polling interval;
- cancellation;
- no signing;
- no automatic execution;
- stable JSON-lines option for automation;
- each observation includes its own time/network/policy identity.

## 16. Stable JSON and exit semantics

Define one schema version and document it.

Suggested common error envelope:

```json
{
  "schema_version": 1,
  "command": "payment.send-strict",
  "ok": false,
  "error": {
    "kind": "quote_expired",
    "message": "..."
  }
}
```

Do not leak secret keys or full sensitive signing payloads into normal errors.

The command layer should map stable error kinds to predictable exit codes. The exact numeric mapping should be documented centrally and tested. At minimum distinguish:
- invalid input;
- no route;
- stale/expired plan;
- preflight/policy rejection;
- transport unavailable;
- signing failure;
- submission failure.

Avoid scattering `process::exit` calls through domain modules.

## 17. Threat model

### Stale route

Risk: user signs a path based on an old market observation.  
Controls: local quote TTL, finite transaction time bounds, exact `dest_min/send_max`, refresh+reconfirm.

### Route tampering

Risk: persisted quote or offline bundle path altered.  
Controls: canonical quote digest + transaction body digest + decoded-XDR comparison.

### Issuer lookalike

Risk: asset code same, wrong issuer.  
Controls: asset identity always code+issuer; show abbreviated and full issuer in machine output/inspect; policy matches exact identity.

### Wrong network

Risk: signing under wrong passphrase or querying one network/building for another.  
Controls: quote/plan bind network passphrase SHA-256; signer uses exact passphrase; mismatch hard-fails.

### Float precision

Risk: rounding creates wrong bound or ranking.  
Controls: integer Amount7 and checked floor/ceil only.

### Malicious/partial Horizon response

Risk: malformed candidate accepted or false ranking.  
Controls: strict record validation, deterministic reject diagnostics, no usable route if all invalid, exact on-chain bound.

### Hidden payment mutation

Risk: automatic sequence/fee retry changes body after approval.  
Controls: immutable plan/body digest, no body-mutating retry after confirmation.

### Secret disclosure

Risk: secret keys/errors/offline artifacts expose signing material.  
Controls: use existing protected wallet flow, zeroize where possible, redact errors, offline bundle has no secrets, 0600 atomic files.

### Hardware capability confusion

Risk: UI claims Trezor signing when implementation cannot do it.  
Controls: capability detection + explicit unsupported error; no fallback to software without user choice.

## 18. Hostile / negative test matrix

### Amount
- 0 rejected;
- negative rejected;
- exponent notation rejected;
- >7 decimals rejected;
- i64 overflow rejected;
- exact max representable boundary;
- slippage multiply overflow rejected;
- strict-send floor correct at one-stroop boundary;
- strict-receive ceil correct at one-stroop boundary.

### Asset
- native round trip;
- 4-char/12-char asset;
- invalid code chars;
- too-long code;
- malformed issuer;
- same code/different issuer remains distinct.

### Horizon
- no routes;
- one valid record;
- deterministic tie;
- malformed amount in one record;
- missing path field/default handling according to actual API schema;
- malformed sibling + valid sibling;
- all malformed => nonzero;
- timeout;
- 429/5xx bounded retry behavior;
- wrong network config.

### Ranking
- strict-send chooses maximum destination;
- strict-receive chooses minimum source;
- policy deny beats better amount;
- hop tie-break;
- lexicographic final tie;
- input response order does not change chosen route.

### Destination
- account absent;
- credit trustline absent;
- unauthorized trustline;
- wrong issuer;
- native destination account exists;
- preflight goes stale before signing.

### Quote freshness
- fresh accepted;
- exactly expired rejected;
- network changes;
- policy changes;
- refreshed route changes => old confirmation invalid;
- refreshed amount changes but path same => old confirmation still invalid.

### XDR
- strict-send golden XDR;
- strict-receive golden XDR;
- path order exact;
- destination exact;
- memo variants;
- time bounds present;
- overlong path rejected;
- decode/encode round trip;
- transaction-body hash changes if any payment parameter changes.

### Signing
- software Ed25519 signature verifies;
- wrong network passphrase signature fails;
- Ledger capability path deterministic with fixture/mocked signer;
- Trezor unsupported path explicit unless feature implemented;
- signature append cannot change body;
- signed imported envelope with mismatched plan rejected.

### Offline
- file mode 0600 on Unix;
- interrupted write leaves prior file intact;
- oversized import rejected;
- future schema rejected;
- body hash tamper rejected;
- quote digest tamper rejected;
- expired bundle non-submittable;
- inspect works without network.

### Submission
- exact confirmed envelope submitted once;
- `txBAD_SEQ` returns rebuild-required, does not mutate and retry;
- insufficient fee requiring body change returns reconfirm-required;
- path operation failure returns refresh-required;
- transport timeout does not claim failure/success ambiguously;
- successful Horizon response hash preserved.

### CLI
- every required subcommand help;
- JSON schema version stable;
- no banner/no decorative output in JSON mode;
- stable error kind/exit code;
- dry-run never signs/submits;
- `--export-unsigned` never writes secret material;
- watch never auto-executes;
- Ctrl-C exits watch cleanly.

## 19. 1,000+ production-line decomposition

The issue minimum should be satisfied by necessary functionality, not padding.

Realistic production split:

- `payment/domain.rs`: ~250–350 LOC
- `payment/transport.rs`: ~220–320
- `payment/ranking.rs`: ~160–240
- `payment/preflight.rs`: ~180–260
- `payment/xdr.rs`: ~240–340
- `payment/signing.rs`: ~140–220
- `payment/plan.rs`: ~160–220
- `payment/store.rs`: ~140–200
- `payment/watch.rs`: ~100–160
- command/output integration: ~300–450

This exceeds the requirement through domain behavior before tests/docs/generated files are counted.

## 20. Suggested implementation commits

1. `feat(payment): add exact assets, amounts and route quote domain`
2. `feat(payment): add Horizon strict path transport and deterministic ranking`
3. `refactor(xdr): extract reusable real Stellar envelope signing primitives`
4. `feat(payment): build strict-send and strict-receive envelopes`
5. `feat(payment): add destination/source preflight and stale quote gates`
6. `feat(payment): add private offline bundles and inspect`
7. `feat(payment): add payment CLI and stable JSON output`
8. `feat(payment): add watch and explicit hardware capabilities`
9. `test(payment): add XDR, precision, stale-route and partial-Horizon coverage`
10. `docs(payment): security, recovery, automation and hardware behavior`

## 21. Final validation after assignment

On the exact final head:

```bash
cargo fmt --all --check
cargo deny --all-features check
cargo build --locked
cargo test --locked
cargo clippy --all-targets --all-features --locked -- -D warnings
./scripts/e2e-smoke.sh
```

Additionally:
- run focused payment domain/XDR/CLI suites;
- prove one deliberate stale/tampered offline bundle fails;
- prove one temporary body mutation invalidates the plan digest;
- verify no live network is required in normal CI;
- re-run all required gates after final rebase/update.

## 22. Non-goals / guardrails

- Do not build a proprietary exchange.
- Do not silently replace source/destination/intermediate assets.
- Do not rank with floating-point weighted scores.
- Do not use `f64` for payment amount or slippage calculations.
- Do not extend the current mock payment XDR/signing path as production code.
- Do not silently rebuild sequence/fee and submit after confirmation.
- Do not claim a local quote TTL is a guaranteed market quote.
- Do not expose secret keys in files, output, fixtures, or error chains.
- Do not claim unsupported hardware signing works.
- Do not require a live Horizon endpoint for CI.
- Do not weaken final repository gates to land a large feature.

A correct #76 implementation makes route choice visible, the on-chain price bound exact, the signed body immutable after confirmation, and every recovery path explicit.
