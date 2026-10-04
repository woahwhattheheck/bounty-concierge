# PocketPay mobile #323 candidate

Source-bound implementation packet for **Stellar-PocketPay/pocketpay-mobile#323 — Add mobile transaction hash copy safety copy**.

## Source pin
- upstream main: `c3a24abacb45030eb4fef41aabc46b312aaf54a3`
- `app/payment-success.tsx`: `fae2073e4751933809dd4c5b7d49ea1c740f5193`
- `app/transaction/[id].tsx`: `99113180ff6069476d518d9a8f67bce9eda3c5f2`
- `__tests__/paymentSuccess.test.tsx`: `c688f4cc514d5d7746f4465bb535244b9e89c74b`
- `__tests__/transactionDetail.test.tsx`: `5a6fe5bfbc9b1987148d0de2ea3f23e2146322ec`

## Candidate behavior
- Receipt and transaction detail explain the hash in non-technical language: “This code identifies your payment. Copy it to share or look it up.”
- Receipt copy success is visible as **Copied**, not icon-only.
- Receipt clipboard failure surfaces a clear alert instead of failing silently.
- Detail keeps its existing visible **Copied** state and clipboard-failure alert; only helper copy and hash-button accessibility metadata are added.
- Focused existing screen tests are extended for the new helper, receipt success state, and receipt failure state.

## Candidate postimages
- `app/payment-success.tsx`: `eaf588575b6d9151787245f587bc36125cebb4a4`
- `app/transaction/[id].tsx`: `15ccceb10ec7d070356c5f57a96c72564a74eff0`
- `__tests__/paymentSuccess.test.tsx`: `88206f707552f98a765c1575c85bc54118140866`
- `__tests__/transactionDetail.test.tsx`: `af80cca4b9c3b198e57344085c060a48de98fc82`

## Validation boundary
The exact postimages were checked for the acceptance invariants recorded in `manifest.json`. **No Jest, typecheck, lint, emulator, or device run is claimed here.** The connected GitHub tools could read/write source, but the execution container could not resolve `github.com`, so a runtime workspace could not be created.

Before upstream publication, an execution-capable publisher should apply these four postimages against the pinned source (or reconcile if main moved) and run at minimum:

```bash
npm test -- --runInBand __tests__/paymentSuccess.test.tsx __tests__/transactionDetail.test.tsx
npm run typecheck
npm run lint
```

## Publication state
At selection time the issue was open, unassigned, had zero comments, and had no matching open PR. A Slack source claim was posted. The current GitHub App then returned **403 Resource not accessible by integration** when posting the upstream contribution comment. This installation has read-only upstream access and no installed `woahwhattheheck/pocketpay-mobile` fork.

Do **not** infer assignment, merge, reward, or payment from this packet. Re-read the issue, comments, open PRs, and current main before publishing. If still clear, an upstream-capable publisher should post the contribution comment, create/reuse the owner fork, run the focused checks, and open one PR.

## Attribution
GPT-5.6 Sol / Switchyard / ChatGPT connected-app cloud harness.
