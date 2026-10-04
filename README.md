# PayD634 listener render

This isolated validation branch renders the complete staging Helm chart at `woahwhattheheck/PayD@fd4960e766ccd804ac7a0f99f788efde511a45b1` using Helm 3.22.0. It verifies Service port 8080 against the default backend listener 3001 and a configured listener 4001, including ConfigMap references, matching selectors, and startup/liveness/readiness probes.

The source remains on the original [PR634](https://github.com/Protocol-Guild/PayD/pull/634). Existing publication operation: `PAYD634-CURRENT-DESCRIPTION-HARBOR6907`. This execution adds acceptance evidence only. It neither creates an upstream submission nor changes the source, deploys a cluster, or claims an award/payment.
