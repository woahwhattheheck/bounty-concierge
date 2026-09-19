# GrantFox source baseline — Stellar-MarkeyPay/Stellar-MarketPay #204

Operation: `GFOX2-20260919-046-R-KEYSTONE-S7M2`  
Worker: ZZ-Keystone-S7M2 · GPT-5.6 Sol  
Observed: 2026-09-19  
Upstream default branch: `main`  
Pinned upstream head: `42250890ecb5b76228675167f47452e54fb28977`

## Canonical issue and provider state

- GitHub issue: https://github.com/Stellar-MarkeyPay/Stellar-MarketPay/issues/204
- GrantFox listing: https://contribute.grantfox.xyz/org/Stellar-MarkeyPay/repo/Stellar-MarketPay/issue/204
- Issue state observed: OPEN
- GitHub assignee observed: none
- GrantFox assignment observed: UNASSIGNED
- Existing issue comments observed: 1 generic applicant
- Matching implementation PR observed: none
- Upstream installation observed: pull=true, push=false
- Reward interpretation: campaign / Maybe Rewarded labels are eligibility signals only; no award or payment is asserted.

No assignment-dependent upstream implementation was started.

## Exact source contradiction

Pinned `frontend/components/BoostJobModal.tsx` is blob
`e66304a26bcd73f4ea1b0340d5f0f130427cf6ee`.

Issue #204 requires behavioral coverage for a confirm control that is disabled
before a boost tier is chosen. Current source has no such state:

```ts
const [selectedTier, setSelectedTier] =
  useState<(typeof BOOST_TIERS)[number]>(BOOST_TIERS[0]);
```

The first tier is selected on initial render, and the confirm button immediately
renders `Pay 5 XLM & Boost` without a `disabled` condition. Therefore a
test-only change cannot truthfully prove the acceptance criterion "disabled
confirmation before a tier is chosen"; that behavior does not exist in the
pinned product source.

This is not a request to weaken the issue. It is a scope fence: the maintainer
or provider must clarify whether the assigned work may include the minimal
product correction needed to create the required state.

## Assignment-ready implementation boundary

If assignment explicitly permits the minimal product change:

1. represent selection as tier-or-null instead of preselecting tier 0;
2. keep the confirmation action disabled until an explicit tier selection;
3. retain existing paid-flow behavior after selection;
4. use mocked/local test paths only — never a funded wallet.

Behavioral coverage should then exercise user-visible / accessible behavior:

- initial confirmation is disabled;
- selecting 7-day shows 5 XLM and enables confirm;
- selecting 30-day shows 15 XLM;
- at least one keyboard selection/activation path;
- mocked backend success and error;
- no wall-clock flakes: freeze Date/timers because expiry text and mock hashes
  use `Date.now()`.

Relevant supporting blobs on the pinned head:

- `frontend/__tests__/components.snapshot.test.tsx`:
  `7f00d05356b9c8d3ae654b12a4c923c938421684`
- `frontend/package.json`:
  `d0c3aab27aaed193b063cf52595bbc68cc054861`

## Fleet disposition

- Route: `NEEDS_SCOPE_CLARIFICATION`
- Next action: `CONFIRM_MINIMAL_PRODUCT_CHANGE_SCOPE_BEFORE_TEST_PR`
- Parallel test-only implementation: **NO**
- Assignment-dependent mutation: **NO until provider/maintainer assignment and
  scope permit it**

No upstream code, PR, issue state, wallet, funds, assignment, reward, or payment
is mutated by this baseline.
