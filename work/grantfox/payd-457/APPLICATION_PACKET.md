# PayD #457 — source-specific application packet

Use only after a fresh GrantFox/GitHub census confirms #457 is still unassigned and has no active implementation carrier.

## Draft

I'd like to take Protocol-Guild/PayD #457. I reviewed current `main@af5c348e83033ed3340e589b68e8554f0303060e` rather than relying only on the issue text.

The reported bug is still present exactly as described: all four methods in `frontend/src/services/withdrawal.ts` catch axios failures and fabricate plausible success data. Anchor discovery returns hard-coded anchors, initiation invents a transaction ID and interactive URL, status polling fabricates a pending transaction, and cancellation suppresses the error.

The existing hook already has the right error-state seam, so I would remove the service-level fake-success behavior and let the hook receive a real or small typed error. I also found one adjacent source bug that matters to the acceptance criteria: `WithdrawalFlow.handleConfirmWithdrawal()` awaits initiation and then checks the pre-await render's `state.step` before calling `onSuccess()`. Because that React closure is stale—and because the hook's initiation failure path returns to `enter_amount`, not `failed`—the success callback can run after a failed initiation. I would make initiation return an explicit success/result (or invoke success only inside the actual success branch) instead of inferring success from React state immediately after await.

Tests would cover network, auth/client, and 500 failures for all four service methods and prove they reject rather than emit mock anchors/IDs/status/success. For the UI acceptance path, I would use the repo's existing browser-test seam to force anchor/initiation failures and assert the error is visible, no fake transaction/interactive URL appears, and no success callback behavior occurs. Positive backend responses would remain unchanged.

I would keep the patch frontend-only as requested, avoid leaking raw backend internals into the UI, and re-census current main/PRs immediately before starting so the implementation composes with any intervening PayD work.
