import type { ComputedRef } from "vue";
import { createAbby } from "../src";

// Compile-only contract; composables execute in component setup, not here.
export function typeContract() {
  const abby = createAbby({
    projectId: "typed", environments: ["test"], currentEnvironment: "test",
    flags: ["sale"], tests: { button: { variants: ["a", "b"] } },
    remoteConfig: { title: "String", price: "Number", options: "JSON" },
  });
  const variant: ComputedRef<"a" | "b"> = abby.useAbby("button").variant;
  const mapped: ComputedRef<1 | 2> = abby.useAbby("button", { a: 1, b: 2 }).variant;
  const flag: ComputedRef<boolean> = abby.useFeatureFlag("sale");
  const title: ComputedRef<string> = abby.useRemoteConfig("title");
  const price: ComputedRef<number> = abby.useRemoteConfig("price");
  const options: ComputedRef<Record<string, unknown>> = abby.useRemoteConfig("options");
  // @ts-expect-error unknown test names are rejected
  abby.useAbby("missing");
  // @ts-expect-error lookup must cover every configured variant
  abby.useAbby("button", { a: 1 });
  // @ts-expect-error unknown flag names are rejected
  abby.useFeatureFlag("missing");
  // @ts-expect-error unknown remote config names are rejected
  abby.useRemoteConfig("missing");
  // @ts-expect-error a Number config does not return a string
  const wrong: ComputedRef<string> = abby.useRemoteConfig("price");
  return { variant, mapped, flag, title, price, options, wrong };
}
