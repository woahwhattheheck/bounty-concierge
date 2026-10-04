# PayD #634: actual Helm listener validation

**Two complete staging-chart renders passed** against source `fd4960e766ccd804ac7a0f99f788efde511a45b1`, the published head of [original PR #634](https://github.com/Protocol-Guild/PayD/pull/634).

| Case | Ingress backend port | Service port | Named container port | ConfigMap PORT |
| --- | ---: | ---: | ---: | --- |
| Service override | 8080 | 8080 | 3001 | "3001" |
| Custom listener | 8080 | 8080 | 4001 | "4001" |

Both renders contain eight Kubernetes objects. Each checks that the Service targets the named `http` port, its selector matches the backend pod, the container references the correct ConfigMap, and startup/liveness/readiness probes use that same named port. The rendered backend Ingress route points to Service port 8080.

The test inputs follow the existing `charts/payd/VALUES.md` instructions: the staging refresh-secret input comes from a file containing `RENDER_ONLY_NOT_A_SIGNING_KEY`, and the backend Ingress port is set consistently with the Service. This is a rendering fixture, not an operational credential.

## Execution evidence

- [Passing run 37208208627, job 111453777592](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37208208627/job/111453777592)
- Controller: `a7abe7dd93273e4fb2d6d6dbeb00f3b412b8d66b`
- Helm `v3.22.0+g144ca65`, Ubuntu 24.04, Python 3.12.3, PyYAML 6.0.2.
- Executed backend Deployment blob: `c47947dda4708079e7c876b9fc632748c4fc2d9e`.
- [Rendered manifests and receipt artifact](https://github.com/woahwhattheheck/bounty-concierge/actions/runs/37208208627/artifacts/11305039315): 6,143 bytes; reported SHA-256 `1119952e1f2c9022d2655f94b33f35c5bcc367e8bc363e0752a959757247d450`; expires October 11. Its metadata and exact job-log receipt were read through the native API; the archive was not downloaded.
- Durable details: `evidence/receipt.json` and `evidence/validation.log` in this branch.

The first run, 37208015675 / job 111453195518, omitted the required staging refresh-secret input and correctly failed at the existing chart guard before either render. That invocation is retained as a setup failure; only the successor above supplies passing listener evidence.

## Delivery scope

This review adds actual chart-render evidence to Kestrel's released listener fix. It changes no PayD production source and runs no application suite or Kubernetes deployment.

The same existing publication operation remains `PAYD634-CURRENT-DESCRIPTION-HARBOR6907`, with Harbor / owner293 custody. Its cumulative description should cite this source and result. There is no new submission or publisher retry. Issue #556 compensation remains conditional GrantFox / Maybe Rewarded; award and payment are unconfirmed.
