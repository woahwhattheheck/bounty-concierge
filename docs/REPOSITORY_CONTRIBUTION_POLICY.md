# Repository terms for an automated upstream submission

The qualifier can consume the existing `submission-policy-context/v1` sidecar
when the caller explicitly declares `submission_method=automated_upstream_submission`.
It recognizes direct prohibitions on generated submissions and autonomous
repository actions. This prevents a known, applicable restriction from being
lost between source inspection, preflight, capture and offline routing.

The input is optional. Missing context or method, private analysis and
`owner_fork_construction` retain the existing qualification behavior. Monetary
qualification and payment readiness are independent of this scoped result.

## Collect once, reuse for the actual target

Use the existing collector to retain one observation for a submission repository:

```sh
python -m concierge.submission_policy_context OWNER/DELIVERY --out private/policy.json
```

Then pass the same sidecar to the existing preflight CLI:

```sh
python -m concierge.bounty_preflight OWNER/BOARD 123 \
  --submission-target private/target.json \
  --submission-method automated_upstream_submission \
  --submission-policy-context private/policy.json \
  --capture private/capture.json --json
```

`target.json` uses the existing `repository`, `source_url`,
`source_content_sha256` and `instruction_excerpt` contract. Omit
`--submission-target` when the issue repository is also the submission target.
The context is matched to the validated target repository, or to the canonical
issue repository when no target is supplied. A different repository's sidecar
cannot block the target or grant it a writer exception.

The Python API accepts the same two optional keyword arguments:

```python
result = preflight_bounty(
    "OWNER/BOARD", 123,
    session=session,
    submission_target=target,
    submission_method="automated_upstream_submission",
    submission_policy_context=context,
    include_capture=True,
)
```

For a normalized offline snapshot, supply `repo`, the optional
`submission_target`, `submission_method` and `submission_policy_context` to
`qualify_dispatch`. The existing `bounty_qualification` JSON CLI accepts these
fields as well. Matching adds no provider requests: callers choose when to
collect or refresh the context and can reuse it across a target's shortlist.

## Evidence and interpretation

The context comes directly from `concierge.submission_policy_context`:

- `submission_repo`, `snapshot_sha` and `observed_at` identify the observation.
- `repository_role.status`, `.source_url` and `.permissions.push` carry the
  submitting account's reported upstream role.
- `documents` contains the requested paths and their read statuses. A `READ`
  record supplies `text`, a revision-pinned `source_url`, `content_sha256` and,
  when reported, `git_blob_sha`.

The matcher checks document bytes against those digests and binds their URLs
to the same repository and snapshot. A writer exception requires a boolean
`push=true` from a `READ` role observation whose source URL names that exact
upstream repository. Permission on an owner's fork does not satisfy it.

Digests bind retained bytes; they do not authenticate the caller or prove that
permissions remain current. The collector's read statuses and observation time
remain in the retained context. This is deliberately a precise matcher for
explicit terms, not a general interpretation of arbitrary contribution policies.
Quoted/code examples and conditional prose such as “unless reviewed and
disclosed” do not become unconditional prohibitions.

## Outcomes

The ordinary result gains a `signals.repository_contribution_policy` entry only
when the automated upstream method and context are supplied. It contains source
identities and interpretation, without document text.

| Evidence for the declared action | Policy status | Effect on existing qualification |
| --- | --- | --- |
| Explicit prohibition applies to observed non-writer | `PROHIBITED_FOR_METHOD` | Add `AUTOMATED_UPSTREAM_SUBMISSION_PROHIBITED`; hold this method |
| Explicit prohibition applies to everyone | `PROHIBITED_FOR_METHOD` | Hold this method regardless of role |
| Confirmed non-writer-scoped rule, role unavailable or from another repository | `UPSTREAM_ROLE_UNKNOWN` | Add `AUTOMATED_SUBMISSION_UPSTREAM_ROLE_UNKNOWN` |
| Same-upstream observed writer is outside the rule's explicit scope | `WRITE_ROLE_EXEMPT` | Keep existing decision |
| Policy names another submission repository | `DIFFERENT_SUBMISSION_TARGET` | Keep existing decision; grant no exception |
| No unconditional prohibition recognized | `NO_EXPLICIT_PROHIBITION` | Keep existing decision |

Missing method/context and private/owner-fork methods add no policy signal or
hold. Existing `REJECT` reasons retain precedence over a policy `HOLD`.

## Capture and replay

The two optional inputs are retained in the private capture's `baseline`, under
the existing consistency digest. Replay supplies the captured canonical repo
and explicit target to the same qualifier, then the offline supply router keeps
the resulting disposition. Legacy captures without these fields retain their
existing schema and behavior. No general test tree, queue or approval step is
introduced.

## Focused execution evidence

The actual Fluxer policy and contributing guide at
`f3c777b2446961352b7153a19e347c26ec74ba8e` exercised the non-writer, writer and
unknown-role paths. The policy Git blob is
`d2c389a64a9fb82057f3cc10771247035da39ca0`.

Temporary focused execution covered wrong-target and fork-role inputs, the
explicit target overriding the issue board, allowed assistance and a
review/disclosure exception, missing optional inputs, private/fork work,
preflight through capture/replay/offline routing, and CLI forwarding/input
errors. Provider-shaped immutable responses exercised the caller path without
live provider traffic; these checks are not live permission or payout evidence.

The collector implementation at `960cc9e66b0bfe78f422757a83dc20c3eddd987a`
also consumed six controlled retained responses, including the actual Fluxer
document bytes, and made zero provider requests. Its returned context fed the
qualifier unchanged. A separate board issue with the validated delivery-target
record retained `HOLD` through preflight, capture, replay and offline routing.
