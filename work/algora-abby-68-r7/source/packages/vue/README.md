# @tryabby/vue

Vue 3.5+ integration for Abby. The core client still owns assignment, targeting,
cookie consent, fallback values and HTTP requests. This package adds typed,
reactive access and handles the lifetime of its subscriptions.

```ts
// abby.ts
import { createAbby } from "@tryabby/vue";
export const abby = createAbby({
  projectId: "YOUR_PROJECT_ID",
  currentEnvironment: "production",
  environments: ["production"],
  tests: { checkout: { variants: ["control", "new"] } },
  flags: ["sale"],
  remoteConfig: { headline: "String", price: "Number", settings: "JSON" },
  cookies: { disableByDefault: true },
});
export const { useAbby, useFeatureFlag, useRemoteConfig, useAbbyStatus } = abby;
```

Install the returned SDK once before mounting:

```ts
import { createApp } from "vue";
import App from "./App.vue";
import { abby } from "./abby";
createApp(App).use(abby).mount("#app");
```

Then call composables synchronously in component setup:

```vue
<script setup lang="ts">
import { useAbby, useFeatureFlag, useRemoteConfig } from "./abby";
const { variant, onAct } = useAbby("checkout", {
  control: "Checkout",
  new: "Order now",
});
const sale = useFeatureFlag("sale");
const price = useRemoteConfig("price");
</script>
<template>
  <button @click="onAct">{{ variant }}: {{ price }}</button>
  <span v-if="sale">Sale</span>
</template>
```

`variant`, flags and config values are read-only computed refs; use `.value` in
JavaScript. Unknown names and incomplete variant mappings are type errors.
`onAct()` reports the current **raw** variant, not its lookup label. An exposure
is sent on mount and when the raw selected variant changes.

As an alternative to the plugin, wrap descendants in the `AbbyProvider` returned
from the **same** factory. It renders only its default slot. An optional
`initialData` prop accepts `AbbyDataResponse`; the plugin accepts the same data as
`.use(abby, { initialData })`. Supplying it skips the initial request. Otherwise
data is loaded once per installed app/provider. Do not combine both approaches
for the same subtree. Initial data is a mount-time seed, not a watched prop.

`useAbbyStatus()` returns read-only `loading` and `error` refs for the initial
load. All consumers in an app/provider share one subscription. Unmounting stops
it, and a late request completion does not update disposed Vue state. Separate
factory calls have separate injection keys. Create a separate SDK per project;
this client integration does not implement server-rendering helpers or devtools.
Cookies honor the core client's consent and expiry settings.

From the repository root:

```sh
pnpm --filter @tryabby/core build
pnpm --filter @tryabby/vue typecheck
pnpm --filter @tryabby/vue test
pnpm --filter @tryabby/vue build
```

The focused suite covers reactive updates, raw-variant tracking, provider
seeding, shared load/subscription cleanup, failure reporting and project
isolation. `test/types.ts` checks inference and rejected names with TypeScript.
