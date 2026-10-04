## Summary
- add brief non-technical helper copy explaining the transaction hash on receipt and detail screens
- show visible “Copied” feedback on the receipt
- surface receipt clipboard failures instead of silently ignoring them
- preserve the existing detail-screen copy success/failure behavior

Closes #323

## Validation
Run before publishing:
- `npm test -- --runInBand __tests__/paymentSuccess.test.tsx __tests__/transactionDetail.test.tsx`
- `npm run typecheck`
- `npm run lint`

## Accessibility
The receipt keeps its copy button label/role and exposes the copied state as visible text; the detail hash copy button receives the same explicit accessibility label/role.
