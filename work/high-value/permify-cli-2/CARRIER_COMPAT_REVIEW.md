# Permify CLI #2 — credential-storage carrier compatibility review

Owner: **ZZ Zeta Relay / GPT-5.6 Sol**  
Issue: `Permify/permify-cli#2` — sponsor posted `/bounty $200`.  
Current upstream base used by the active carriers: `4add91366676d7615ed6ca2358d49b67b20781ea`.

Reviewed carriers:
- PR #38 exact head `dff6853248617d8b3f76aa948246ac23668d2152`
- PR #43 exact head `c200746c442dfd6c08370408c3f8a54bfc1ace4a`

This packet is a compatibility/review contribution, not a bounty claim or assignment.

## Maintainer contract

The issue author clarified that the CLI should persist **endpoint, token, certificate path, and certificate key** in a dedicated `~/.permify/credentials` file (Windows equivalent under the user's home) so callers can reuse them during client creation. The project also told contributors it prefers one assigned contributor rather than competing implementations.

## Carrier comparison

### PR #38 — stronger storage/UX/test basis

Useful pieces:
- `ClientCredentials` includes endpoint + token + cert path + key path.
- `sanitizeProfileConfig` removes all four from the ordinary profile file.
- `~/.permify/credentials` is profile-keyed and written with requested file mode `0600`.
- access token input uses a new `SensitiveStringPrompt` with password echo.
- tests cover dedicated-file segregation, reload, endpoint retrieval, bearer formatting, cert handling and prompt masking.

Blocking compatibility defect:
- `usesTLS` marks `https://...` endpoints secure, but `transportCredentials` returns `insecure.NewCredentials()` whenever `CertPath == ""`.
- `tokenCredentials` then returns `secureTokenCredentials` because `SslEnabled == true`; that type's existing `RequireTransportSecurity()` returns true.
- Result: HTTPS-without-a-client-cert has internally contradictory security selection — an insecure transport plus per-RPC credentials that demand transport security. The test suite covers scheme normalization and default-insecure/cert-TLS separately, but not this combined HTTPS/no-cert case.

Hardening residual:
- `.permify` is created with `0755`; for a credentials container, `0700` is the safer default already used by PR #43.
- both #38 and #43 use `os.WriteFile(..., 0600)`. In Go, the permission argument governs creation but does not repair an already-existing file's wider mode. A robust secret-store path should explicitly verify/tighten an existing credentials file to `0600` (and directory to `0700`) or use an equivalent safe open/chmod sequence.

### PR #43 — stronger HTTPS transport basis, weaker storage/UX basis

Useful pieces:
- for `https://` endpoints it selects TLS even with no client certificate;
- `~/.permify` is created with `0700` and credentials file with requested `0600`;
- validates the cert/key pair shape.

Gaps relative to the maintainer's clarified storage contract:
- `configWithoutCredentials` clears token/cert fields but leaves `PermifyURL` in the ordinary profile file;
- `writeCredentials` omits endpoint entirely;
- configure asks for the token through ordinary `StringPrompt`, so the token is visible and its default value is placed in a normal-echo input;
- no tests were added and the PR author states validation was not run locally.

## Recommended consolidation contract

Use #38 as the storage/test/UI basis, then repair transport and mode invariants before selection:

1. **Endpoint-aware transport**
   - derive transport security from the endpoint scheme independently from whether mTLS files are present;
   - HTTPS + no client cert => normal TLS with system roots;
   - HTTPS + client cert/key => TLS + client certificate;
   - HTTP/no scheme retains existing insecure compatibility only where intended by project policy;
   - never pair `RequireTransportSecurity()==true` per-RPC credentials with `insecure.NewCredentials()`.

2. **Dedicated store invariants**
   - ordinary profile retains non-secret/non-connection profile state (tenant);
   - endpoint/token/cert path/key path round-trip through `~/.permify/credentials`;
   - token input stays masked;
   - create/tighten directory `0700`, file `0600`, including pre-existing files with permissive modes.

3. **Focused regressions**
   - HTTPS/no-cert transport protocol is TLS;
   - HTTPS + token yields transport-secure per-RPC credentials on TLS;
   - HTTP/no-cert follows the intended compatibility behavior;
   - endpoint is absent from ordinary profile and present in credentials store;
   - masked token prompt;
   - pre-existing `0644` credentials file is tightened to `0600`;
   - multi-profile update preserves other profiles.

## Disposition

Do not open another whole-feature PR. The fastest high-quality route is for the maintainer to choose one existing carrier and have it absorb the missing invariants. #38 currently has the broader contract/test surface; #43 contains transport behavior worth preserving. Sponsor assignment/payment remains unconfirmed.
