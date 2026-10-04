# PayD634 listener render

This isolated validation branch renders the complete staging Helm chart at `woahwhattheheck/PayD@fd4960e766ccd804ac7a0f99f788efde511a45b1` using Helm3.22.0. It verifies Service port8080 against the default backend listener3001 and a configured listener4001, including ConfigMap references, matching selectors, and all three probes.

Both cases follow the existing VALUES.md guidance: the backend Ingress targets Service8080, and the required staging refresh-secret input comes from a file containing `RENDER_ONLY_NOT_A_SIGNING_KEY`. It is an inert rendering fixture, not an operational signing key. Rendered Ingress ports are checked as well.

The source remains on original [PR634](https://github.com/Protocol-Guild/PayD/pull/634). Existing publication operation: `PAYD634-CURRENT-DESCRIPTION-HARBOR6907`. This execution adds acceptance evidence only; it neither creates an upstream submission, changes production source, deploys a cluster, nor claims an award/payment.

The first run37208015675 correctly rejected the omitted required staging secret before either case rendered. The amended controller supplies the documented fixture; its execution result will be recorded separately.
