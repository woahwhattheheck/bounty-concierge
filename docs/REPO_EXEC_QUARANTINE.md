# Repo execution quarantine — targeted incident preflight

**Purpose:** stop the fleet from executing a known hostile, obfuscated remote-execution loader carried by `eslint.config.js` in several October 2026 bounty repositories. This is a targeted source fingerprint check, not a generalized malware scanner or authorization to run code.

Use **before** npm install, lint, tests, builds, dev servers, CI, or any process that might import arbitrary sponsor JavaScript:

```sh
python -m concierge.repo_exec_quarantine /path/to/untrusted/checkout --json
```

The script reads root `eslint.config.js`, `.mjs`, and `.cjs` **as bytes only**; it never loads the configuration or calls a subprocess. Known dangerous Git blob `7da565bcb57517fa1c3adc1c824b7e105dae2699` produces `QUARANTINE` with exit 2. An unfamiliar config containing encoded content plus both `eval()` and process execution receives `REVIEW_REQUIRED` with exit 3. Unreadable, oversized, or symlinked configs also require review. Exit 0 means only `NO_KNOWN_INDICATOR`: it does **not** mean a checkout is safe or a payer is verified.

**Verified incident source links:** [Sampled compromised commit](https://github.com/sampled-labs/sampled/commit/08a2d1db8317e1620ead99f8eb7bdfe1e002aef0), [BalloonFly committed loader](https://github.com/balloonfly-hq/hackathon-scaffold-stellar/commit/94192afc57ac20da6b9aa8c9a0254be995341792), [Scaffold Studio committed loader](https://github.com/scaffold-studio-hq/scaffold-studio-contracts/commit/7937aa5e1574be3830d37d0cfa8c1db94d80b172). Preserve historical evidence; do not execute the malicious file or echo any decoded remote payload.

**Important operational limitation:** The rule covers only root ESLint configs and two observed static heuristics. A clean result cannot override existing security quarantine, payer-proof hold, acceptance review, or credentialed-runner controls. Sponsor forks, checkout branch changes and transitive dependencies require separate source inspection. Always confirm the current repository/branch and original commit identity before rescanning; do not treat a clean fork as proof its upstream is fixed.

Validation: five focused standard-library tests in `tests/test_repo_exec_quarantine.py` using synthetic fixture data only. No network, npm, sponsor code execution or broad tests are needed.
