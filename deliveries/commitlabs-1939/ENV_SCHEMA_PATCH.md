# env.ts schema patch

Upstream preimage blob: `8e6a527cc1c798a5db217e30b5fc23f9a549856c`.

In `src/lib/backend/env.ts`, add this recognized optional variable immediately after the existing `COMMITLABS_FEATURE_FLAGS_JSON` field:

```ts
COMMITLABS_SUPPORTED_CONFIG_JSON: z.string().optional(),
```

Do not replace the whole upstream file from this packet. The pinned upstream blob already contains ten unrelated literal `zZ.*` tokens; re-read current upstream and apply only the single schema field after any base repair/rebase.
