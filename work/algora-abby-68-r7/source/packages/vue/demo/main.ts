import { createApp, defineComponent, h } from "vue";
import { createAbby } from "../src";
const abby = createAbby({
  projectId: "local-demo", currentEnvironment: "test", environments: ["test"],
  apiUrl: "https://demo.invalid", cookies: { disableByDefault: true },
  tests: { checkout: { variants: ["control", "new"] } },
  flags: ["sale"], remoteConfig: { price: "Number" },
});
const Component = defineComponent({ setup() {
  const { variant, onAct } = abby.useAbby("checkout", { control: "Checkout", new: "Order now" });
  const sale = abby.useFeatureFlag("sale");
  const price = abby.useRemoteConfig("price");
  return () => h("main", { style: "font: 24px system-ui; padding: 48px" }, [
    h("h1", "Abby + Vue"),
    h("p", "Local fixture demo — no live project or production events"),
    h("p", { id: "state" }, `${variant.value} · Sale: ${sale.value} · Price: ${price.value}`),
    h("button", { id: "act", onClick: onAct }, "Track conversion"),
    h("button", { id: "update", onClick: () => {
      abby.__abby__.updateLocalVariant("checkout", "new");
      abby.__abby__.updateFlag("sale", true);
      abby.__abby__.updateRemoteConfig("price", 15);
    }, style: "margin-left: 20px" }, "Update core state"),
    h("p", "The rendered values come from the package's computed composables."),
  ]);
} });
createApp(Component).use(abby, { initialData: {
  tests: [{ name: "checkout", weights: [100, 0] }],
  flags: [{ name: "sale", value: false }],
  remoteConfig: [{ name: "price", value: 20 }],
} }).mount("#app");
