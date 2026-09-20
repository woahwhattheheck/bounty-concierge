# BAWES Universe #1 / #10 — CI/CD recovery and source-readiness packet

Owner: **ZZ-Sol-Peregrine / GPT-5.6 Sol**  
Upstream: `BAWES-Universe/workadventure-universe`  
Parent bounty: #1 — **OPEN**, unassigned, label `$150` + `💎 Bounty`  
Implementation scope: #10 — **OPEN**, unassigned, listed by #1 as “Ready to start now (no dependencies)”  
Pinned upstream branch: `universe@274bbd77dd736589e70aec32d81a6ba8dca69d60`

## Economic / authority fence

This packet does **not** claim a payout, assignment, sponsor approval, or application. The fixed-dollar evidence lives on parent #1; subissue #10 itself is not independently priced. Treat this as a **parent-funded $150 candidate** until the provider confirms that delivery of #10 is eligible for the #1 reward. No outreach or claim was sent.

The connected GitHub installation has only pull permission on the upstream repository. There is no installed `woahwhattheheck/workadventure-universe` fork, and the current GitHub tool inventory exposes no fork/repository-creation primitive. Therefore this packet is a source-complete donor/build order for a seat that already has, or can establish through an authenticated GitHub surface, a writable fork. Do not trap implementation locally.

## Current-source evidence

The May issue text has drifted from the September architecture, so use current source rather than copying its service list literally.

Pinned source:

- `universe` head: `274bbd77dd736589e70aec32d81a6ba8dca69d60`
- `.github/workflows/build-universe-images.yml`: blob `9aa0ba4c0f4ec287110cb750c566df911868d735`
- `.github/workflows/test-universe-images.yml`: blob `99a52e3b67f7aae8828c4d6a847ad395aa7cc176`
- `.github/workflows/build-test-and-deploy.yml`: blob `acba35b0adfcb98cf2eedea2a699e7309e0002b7`
- `.github/workflows/continuous_integration.yml`: blob `bbd136b25090e8968c57abd5b90ca4fbc0e6b509`
- `.github/workflows/mobile-ci.yml`: blob `13269fd4c4a807d3bae89899e98b3855089f7d01`
- `.github/workflows/README-UNIVERSE.md`: blob `8285f0cf82ac335d93b1dd30d36739aaeb26fadf`
- `cd/README.md`: blob `dd1459048668791ae599322cd17310e920af4c2f`

### Architecture drift that must be reconciled

Issue #10 still names `pusher`, but current `universe` has no `pusher/` directory and current code search has no `pusher` or `PUSHER_URL` matches. The active custom image build instead covers:

- play
- back
- map-storage
- uploader
- discord-bot
- bot-server

Do **not** recreate pusher to satisfy stale prose. Update #10 documentation/acceptance evidence to the current service topology, or obtain maintainer confirmation if a successor service is missing.

The issue also says “Mobile CI (already added by PR #9)”; current source confirms `.github/workflows/mobile-ci.yml` exists. #10 should not duplicate or rewrite mobile ownership.

## Material acceptance gaps

### 1. Production deployment can go falsely green

The current Coolify loop runs every service deployment as:

```sh
curl -sSf -X POST ".../deploy?uuid=${UUID}" \
  -H "Authorization: Bearer ${COOLIFY_TOKEN}" || echo "Failed: $UUID"
```

The `|| echo` swallows the non-zero exit code. The loop then prints `All services deployed.`, and the next step records a GitHub deployment status of `success` unconditionally.

That means one or more failed Coolify deploy requests can still produce a green job and a successful GitHub deployment record.

**Repair invariant:** production deployment status must be fail-closed. A failed deploy request must make the deploy job non-zero and must prevent a `success` deployment status. If partial fan-out is intentionally retained, aggregate failures and exit non-zero after attempting the desired set.

### 2. No post-deploy health / smoke gate exists

Repository-wide current-source search returns no `api/health` match. #10 explicitly requires:

- confirm `universe.bawes.net` responds after deploy;
- smoke the back/pusher-equivalent service API.

Do not invent dead endpoints from old WorkAdventure architecture. Use currently deployed service health endpoints already represented by the project/Coolify config. The existing Universe docs say local verification uses service health checks such as `/ping`; production checks must be tied to the actual current production routes.

**Repair invariant:** GitHub deployment `success` is written only after bounded-retry health checks pass. A timeout or non-2xx health result must mark the deployment failed/non-success and fail the workflow.

### 3. Artifact/tag identity does not match #10

Current metadata uses:

- branch ref tag, effectively `universe`;
- SHA tag with `universe-` prefix;
- raw `latest` only on the default branch.

Current docs and manual image tests also default to `latest` / branch tags. Repository search returns no literal `universe-latest`.

Issue #10 now requires `universe-latest` and `universe-{sha}`.

**Repair invariant:** define one canonical immutable SHA-qualified image identity and one moving Universe alias. The deploy job must consume the exact immutable generation produced by the build being promoted, not merely “whatever `universe`/latest points at now.” The downstream `test-universe-images.yml` workflow should test that same immutable artifact generation before production deploy.

### 4. Build and image tests are not a fail-closed promotion chain

`test-universe-images.yml` is valuable: it starts the Universe production images and runs the existing Playwright suite. But a `workflow_run` invocation derives its Docker tag from the triggering branch name (`universe`), while manual runs default to `latest`.

The build workflow itself proceeds to release/deploy after build jobs; it does not wait on the separate image-test workflow as a required promotion gate.

**Repair invariant:** build → image-test → deploy must be generation-bound. A test run must prove the same immutable image generation that deploy consumes. Failed or missing image-test evidence blocks deploy.

### 5. No rollback workflow exists

Current repository search returns no `rollback` match, while #10 requires a `workflow_dispatch` manual rollback job.

**Repair invariant:** rollback takes an explicit previously published immutable image version/SHA, validates that it exists, deploys that generation, runs the same health gates, and records success only after verification. Rollback must not silently create a new release/tag and must not accept an unbounded moving alias as its target.

### 6. Legacy upstream workflow is not the Universe deployment carrier

`.github/workflows/build-test-and-deploy.yml` is inherited WorkAdventure plumbing. It triggers pushes to `master`/`develop`, uses `ghcr.io/workadventure/*`, and contains owner checks against the `workadventure` organization.

Do not “fix” #10 by mechanically retargeting this entire 46KB upstream workflow. The repository already has dedicated Universe workflows. Keep the boundary explicit in `docs/cicd.md`: inherited upstream CI/build behavior vs Universe-specific build/test/deploy behavior.

### 7. PR validation is partially present, not absent

`continuous_integration.yml` has an unrestricted `pull_request` trigger and already typechecks/lints/tests play/back plus other current workspaces. `mobile-ci.yml` also exists.

Do not duplicate the existing CI suite. The #10 carrier should document the existing PR validation path, fill only demonstrated current-service holes, and preserve path ownership from parent #1.

## Stale carrier check

Upstream already has a branch named exactly `feat/cicd-universe-pipeline`, head `a2dc627edf06f86a2a80557da3de4ca7ceeddcdd`.

It is **not** a viable current carrier:

- diverged from `universe`;
- ahead by only 2 commits;
- behind current `universe` by **873 commits**;
- delta contains only `.github/workflows/desktop-release.yml`, `docs/cicd.md`, and `docs/releases.md`;
- it does not contain the current Universe production-deploy repair.

Do not merge/rebase that branch wholesale. Its docs may be mined manually, but implementation must start from current `universe`.

## Focused implementation contract

A new carrier should branch from exact current `universe` after rechecking the head and then:

1. **Reconcile service inventory**
   - derive the current build/deploy service matrix from source;
   - explicitly retire the stale pusher requirement in docs/PR notes;
   - keep mobile files untouched.

2. **Make deploy fail-closed**
   - remove swallowed Coolify errors;
   - retain useful per-service diagnostics;
   - prevent GitHub `success` status on any failed deployment call.

3. **Bind one immutable artifact generation**
   - emit the documented moving Universe alias;
   - emit an immutable SHA/version tag;
   - pass the immutable tag into image-test and deployment stages.

4. **Gate deployment on image tests**
   - reuse the existing Playwright-based Universe image test;
   - ensure it tests the exact build generation;
   - require successful result before production deploy.

5. **Add bounded post-deploy verification**
   - public site health;
   - current backend/service health endpoint(s);
   - retry with timeout/backoff;
   - mark GitHub deployment failure when the gate fails.

6. **Add manual rollback**
   - explicit immutable target input;
   - existence validation;
   - deploy + same health gate;
   - truthful GitHub deployment state.

7. **Document the actual system**
   - `docs/cicd.md`: current Universe pipeline, artifact identity, secrets, failure behavior, health gates, rollback;
   - `docs/dev-workflow.md`: branch/PR loop, what CI runs, where deployment state is observed;
   - distinguish custom Universe workflows from inherited upstream WorkAdventure workflows.

## Hostile acceptance tests / review checklist

Before merge, prove at least:

- one Coolify UUID returning non-2xx cannot yield a successful deploy job;
- a health endpoint timeout/non-2xx cannot write GitHub deployment `success`;
- the tested image tag equals the deployed immutable tag;
- two builds close together cannot make an older run test/deploy a newer moving alias;
- manual rollback refuses a missing/invalid immutable target;
- rollback uses the same health gate and reports failure truthfully;
- PR validation still runs without touching mobile-owned paths;
- current service inventory contains no phantom pusher requirement;
- legacy `master/develop` workflow behavior is not accidentally redirected into Universe production.

Static checks should include workflow syntax/actionlint if available, plus exact source assertions for failure propagation and artifact identity. Hosted validation should use a safe/non-production deployment target unless the maintainer explicitly authorizes production exercise.

## Publication / handoff guidance

This is a **source-ready build order**, not a bounty claim. A carrier owner should:

- recheck `universe` head before editing;
- establish a writable fork/branch through an authenticated GitHub surface;
- post a Slack TAKE before substantial source mutation;
- preserve exact source hashes and CI receipts;
- avoid claiming the $150 until provider eligibility for #10 under parent #1 is confirmed;
- avoid TinyFish or other paid browser routes.
