# Revenue Response Queue: Decisive Event Precedence

The response queue classifies the **newest decisive provider event** for the current proven engagement generation. Provider order is the existing `(occurred_at, sequence, id)` order; later decisive evidence supersedes earlier decisive evidence.

Decisive evidence is limited to:

- a human inbound linked to the current baseline from an authorized route (`HUMAN_REPLY`);
- a human inbound linked to the current baseline from an unbound route (`HUMAN_REVIEW_REQUIRED`);
- a bounce linked to the current baseline (`ROUTE_REPAIR`); and
- unlinked or ambiguous same-thread activity relevant to an authorized route (`HUMAN_REVIEW_REQUIRED`).

This ordering is fail-closed across offer/generation ambiguity. A linked human reply does **not** remain a safe terminal classification when newer provider evidence is unlinked or ambiguous. Conversely, an older ambiguous event may be superseded by a newer correctly linked human reply because the newer provider event re-establishes an attributable decisive state for the retained baseline.

Automated acknowledgements are not decisive evidence and cannot clear ambiguity. They influence only the follow-up grace path when no decisive event exists.

## Required chronology hostiles

The focused hostile suite permanently covers these cases:

1. linked authorized human reply, then newer unlinked outbound to an authorized route -> `HUMAN_REVIEW_REQUIRED`;
2. linked authorized human reply, then newer unlinked authorized inbound -> `HUMAN_REVIEW_REQUIRED`;
3. older unlinked relevant activity, then newer correctly linked authorized human -> `HUMAN_REPLY`;
4. linked human and linked bounce in either order -> the newer decisive event wins.

This precedence rule changes classification only. It grants no send/reply, provider-write, contact-policy, payment-recognition, or revenue-recognition authority.
