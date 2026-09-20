# POIDH #1334 — Onchain POAPs contract/interface matrix

Status: source-pinned implementation packet; **no bounty claim, wallet signing, deployment, Farcaster/X post, or live-funds action performed**.

## Source fence

- Bounty work order: POIDH Base bounty `1334` (“Onchain POAPs frontend”), advertised as 0.0457 ETH on Base; provider selection remains authoritative.
- Contract repository: `jvaleskadevs/onchain-poaps`.
- Exact upstream source: `main@c313c856cd9f26bbc9e61e4ef12cb3e463409708`.
- Exact contract blob: `src/Poap.sol@d5042a374107bca5d33142485a31159eb31288de`.
- Exact README blob: `README.md@2b16e1939b7c652e0164c62e038a392cc678d532`.
- README-declared Base Sepolia address: `0xC3249356a483fbe17d5355D39105D2eA666d9de6`.
- Base Sepolia chain id: `84532`.

The installed GitHub connection has pull-only access to the upstream repository and no installed `woahwhattheheck/onchain-poaps` fork. This packet therefore does not pretend that a deployable frontend PR was published upstream.

## Contract surface and frontend authority

| Surface | Caller / prerequisite | State effect | Frontend contract |
| --- | --- | --- | --- |
| `registerEvent(name,description,eventDate,location,allowlistRoot,svgImage,externalUrl,flags)` | Any wallet; validates name/description/svg/location/url and `flags <= 3` | Creates next event, stores Base64 SVG through SSTORE2, emits `NewEvent(eventId,name,creator)` | Registration form may offer flags 0–3. **Derive the created event id from the confirmed `NewEvent` receipt, not from a pre-write `totalEvents` read**, because concurrent registrations can advance the counter. |
| `mint(eventId)` | Any wallet; event must exist, be public, and not previously claimed by caller | Mints one ERC1155 token to caller, marks `hasClaimed`, emits `NewMint` | Public-mint CTA should pre-read `events(eventId).isPublic` + `hasClaimed(eventId,account)` for UX, but treat the transaction result as authority. Disable duplicate submissions while pending. |
| `allowlistMint(eventId,proof)` | Caller needs valid Merkle proof; allowlist root nonzero | One mint per wallet | Later flow. Proof generation/distribution is offchain creator responsibility; frontend must not invent membership from wallet connection alone. |
| `mintWithSignature(eventId,signature)` | Signature must recover event creator for `(eventId,chainId,recipient)`; valid through creator timelock + 7 days | One mint per wallet | Later flow. Signature is recipient- and chain-bound; UI must not reuse a signature after wallet/network change. |
| `creatorMint(eventId,recipients[])` | Event creator; within 30 days; max 101 recipients | Batch mints, skipping already-claimed recipients | Creator-only management flow. Surface skipped/already-claimed semantics rather than implying atomic all-recipient delivery. |
| `updateAllowlistRoot(eventId,newRoot)` | Event creator; within 30 days; existing root must be zero | Sets root once | Creator-only; one-way transition. Confirmation copy should make the one-time property explicit. |
| `updateEventPublic(eventId,isPublic)` | Event creator; within 30 days | Toggles public mint state | Creator-only; public-mint view must refresh state after receipt. |
| `events(eventId)` | View | None | Canonical event metadata/state source for list/detail pages. |
| `totalEvents()` | View | None | Pagination/index hint only; never use a stale value to infer a just-created event id. |
| `hasClaimed(eventId,address)` | View | None | Wallet-specific eligibility hint. Transaction revert remains authoritative. |
| `uri(eventId)` | View | None | Returns `data:application/json;base64,...`; frontend can render fully onchain metadata without IPFS/server dependency. |
| `getMultichainEventId(eventId)` | View | None | Must return CAIP-style `eip155:{chainId}:{contract}:{eventId}`; use as stable display/share identifier. |
| ERC1155 `balanceOf` / `balanceOfBatch` | View | None | Collection/owned-token views. Soulbound status is event metadata, not inferred from balance. |

## Registration → public-mint vertical slice

A bounded first implementation should do exactly this before adding the other mint modes:

1. Require Base Sepolia (`84532`) in development/test mode and show an explicit network mismatch state instead of silently switching or signing.
2. Read `totalEvents`, event 0, `getMultichainEventId(0)`, and `uri(0)` through the read-only smoke to prove RPC/address/ABI alignment without a wallet transaction.
3. Registration form enforces the contract’s visible byte/flag bounds client-side but still surfaces contract reverts verbatim enough to diagnose rejection.
4. Submit `registerEvent`; wait for a successful receipt; parse `NewEvent` to obtain the authoritative event id; then read that event back.
5. Event page reads `events`, `hasClaimed`, `balanceOf`, `uri`, and multichain id. Public mint is enabled only when `isPublic && !hasClaimed` in the latest read.
6. Submit `mint(eventId)` only after explicit wallet confirmation; wait for receipt and then refresh `hasClaimed`, `balanceOf`, and metadata.
7. Handle concurrent registration, chain/account changes while a transaction is pending, user rejection, replacement/reorg/failure, already-claimed, non-public, and nonexistent-event states without optimistic success.

## Required regression fence for that slice

- Registration receipt event id wins over `totalEvents` pre-read under a simulated concurrent registration.
- Flag matrix 0/1/2/3 maps exactly to `(isPublic,isSoulbound)` = `(false,false)/(false,true)/(true,false)/(true,true)`; `4` is rejected before signing.
- Empty name or SVG and over-limit name/description/location/url fail before wallet prompt and remain contract-validated if bypassed.
- Public mint CTA is absent/disabled for private event and already-claimed wallet, but a stale pre-read can never be reported as success if the write reverts.
- Account or chain change invalidates wallet-specific eligibility and any prepared signature/proof state.
- `uri()` is accepted only as onchain `data:application/json;base64,...` for the current source contract; malformed decode produces a bounded UI error, not arbitrary HTML execution.
- Creator controls are hidden/disabled for non-creators and after the timelock, but contract reverts remain the final authority.

## Explicitly not proven in this packet

- No Base Sepolia RPC call was executed in this seat because Foundry `cast` is not installed in the available runtime. `READ_ONLY_SMOKE.sh` passed `bash -n` only; it is ready for an execution-capable seat with a free Base Sepolia RPC endpoint.
- No Base mainnet transaction, wallet signature, event registration, mint, contract deployment, or funded action was performed.
- No standalone web app or Farcaster Mini App was created because this GitHub installation has no writable fork of `onchain-poaps` and no repository-creation primitive was exposed. A fork/repo-capable seat should use this exact-source contract before implementing the site.
- The bounty’s deployed-site, Mini App, social/cast, and provider-selection requirements remain outstanding and provider-controlled.
