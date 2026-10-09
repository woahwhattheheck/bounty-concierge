# Repo execution quarantine — targeted incident preflight

**Purpose:** stop the fleet from executing a known hostile, obfuscated remote-execution loader carried by executable `eslint.config.*` and `postcss.config.*` files (including nested monorepo packages) in October 2026 bounty and unrelated repositories. This is a targeted source fingerprint check, not a generalized malware scanner or authorization to run code.

Use **before** npm install, lint, tests, builds, dev servers, CI, or any process that might import arbitrary sponsor JavaScript:

```sh
python -m concierge.repo_exec_quarantine /path/to/untrusted/checkout --json
```

The script traverses the checkout without following symlink directories, inspecting root and nested `eslint.config.{js,mjs,cjs}` and `postcss.config.{js,mjs,cjs}` **as bytes only**. It never imports configurations or calls a subprocess. Traversal is bounded (4,000 directories, 300 config files, depth 16, 2 MB per file); missing/unreadable/symlinked or over-limit coverage produces `REVIEW_REQUIRED` rather than a false clean signal. Known dangerous Git blobs `7da565bcb57517fa1c3adc1c824b7e105dae2699` (BalloonFly, Scaffold, Streamr), `0290e72b7db38a21c14e86357e2002d7f00709e3` (Stellita), `b509afc8d03e6ac6b124a00ebe37f3e0e290d367` (PostCSS), `ea13e03bee28e8a79b9c521179b4abe45c581db2` (ResumeAI PostCSS), and `b20772d40a56f9f2f208d724193bb819d459a2aa` (PrimeX nested ESLint) produce `QUARANTINE` with exit 2. Unknown byte variants with the specific loader-family marker, `NONCE_FANOUT`, and both eval/process-spawn indicators also produce `QUARANTINE`. An unfamiliar config containing encoded content plus both `eval()` and process execution receives `REVIEW_REQUIRED` with exit 3. Unreadable, oversized, or symlinked configs also require review. Exit 0 means only `NO_KNOWN_INDICATOR`: it does **not** mean a checkout is safe or a payer is verified.

**Verified incident source links:** [Sampled compromised commit](https://github.com/sampled-labs/sampled/commit/08a2d1db8317e1620ead99f8eb7bdfe1e002aef0), [BalloonFly committed loader](https://github.com/balloonfly-hq/hackathon-scaffold-stellar/commit/94192afc57ac20da6b9aa8c9a0254be995341792), [Scaffold Studio committed loader](https://github.com/scaffold-studio-hq/scaffold-studio-contracts/commit/7937aa5e1574be3830d37d0cfa8c1db94d80b172). Preserve historical evidence; do not execute the malicious file or echo any decoded remote payload.

**Important operational limitation:** The rule now covers nested ESLint and PostCSS configs plus selected npm lifecycle metadata, VS Code automatic task metadata, and WOFF2 file headers. It does not inspect files launched indirectly by package scripts, all editor features, arbitrary file types, or transitive dependencies. A clean result cannot override existing security quarantine, payer-proof hold, acceptance review, or credentialed-runner controls. Sponsor forks, checkout branch changes and transitive dependencies require separate source inspection. Always confirm the current repository/branch and original commit identity before rescanning; do not treat a clean fork as proof its upstream is fixed.

## Additional static checks

The existing CLI and exit codes now include `concierge.repo_exec_autoexec`. It reads package manifests for lifecycle hook names, editor task configuration for folder-open triggers, and public font files for WOFF2 header bytes. It never executes inspected code. Verified high-confidence source indicators result in `QUARANTINE`; other automatic execution indicators and uncertain inputs require `REVIEW_REQUIRED`. Traversal and byte limits remain bounded. A negative result does not establish repository safety.

Validation: the original six focused standard-library tests and six nested/variant regressions remain. Five new inert source fixtures in `tests/test_repo_exec_autoexec.py` passed in the ChatGPT cloud container. No broad tests or untrusted source execution were performed.

## Cross-repository source observations (October 9)

| Current first-party path | Git blob SHA | Evidence |
|---|---|---|
| `Astrae-Design/Interaction-Design/logo-clouds/postcss.config.mjs` | `b509afc8d03e6ac6b124a00ebe37f3e0e290d367` | https://github.com/Astrae-Design/Interaction-Design/blob/main/logo-clouds/postcss.config.mjs |
| `TheSabari07/MyHealthVault/apps/web/postcss.config.mjs` | `b509afc8d03e6ac6b124a00ebe37f3e0e290d367` | https://github.com/TheSabari07/MyHealthVault/blob/main/apps/web/postcss.config.mjs |
| `israfil-hossain/resumeai/postcss.config.mjs` | `ea13e03bee28e8a79b9c521179b4abe45c581db2` | https://github.com/israfil-hossain/resumeai/blob/main/postcss.config.mjs |
| `TheSabari07/PrimeX/packages/ui/eslint.config.mjs` | `b20772d40a56f9f2f208d724193bb819d459a2aa` | https://github.com/TheSabari07/PrimeX/blob/main/packages/ui/eslint.config.mjs |

In all four owner-source observations, the exact uncommon loader identifier `GSkqNNyuJw$_padNcYwam`, `NONCE_FANOUT`, `eval(`, and process `spawn(` appeared in the executable configuration. This is reason to quarantine **the files**, not evidence of the repository owner's intent. [A distinct September 2026 first-party incident report](https://github.com/Malix55/car-workshop_management_system/blob/main/SECURITY_INCIDENT_2026-09.md) documents the same loader function in a broader build-time malware incident. If any agent actually ran an infected file on a credentialed runner, isolate the runner and investigate potential token/key exposure and rotation. Never infer exposure from code presence alone.

