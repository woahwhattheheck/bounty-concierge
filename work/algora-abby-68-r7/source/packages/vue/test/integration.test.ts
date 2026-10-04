import { AbbyEventType, HttpService, type AbbyDataResponse } from "@tryabby/core";
import { mount, flushPromises } from "@vue/test-utils";
import { createApp, defineComponent, h, nextTick } from "vue";
import { afterEach, expect, it, vi } from "vitest";
import { createAbby } from "../src";

const initialData: AbbyDataResponse = {
  tests: [{ name: "button", weights: [100, 0] }],
  flags: [{ name: "sale", value: true }],
  remoteConfig: [{ name: "price", value: 12 }],
};
function sdk() {
  return createAbby({
    projectId: "vue-test", currentEnvironment: "test", environments: ["test"],
    tests: { button: { variants: ["a", "b"] } },
    flags: ["sale"], remoteConfig: { price: "Number" },
    cookies: { disableByDefault: true },
  });
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });

it("renders typed plugin values, reacts to core updates, and tracks raw variants", async () => {
  vi.stubEnv("NODE_ENV", "development");
  const abby = sdk();
  const send = vi.spyOn(HttpService, "sendData").mockImplementation(vi.fn());
  const Component = defineComponent({ setup() {
    const { variant, onAct } = abby.useAbby("button", { a: "Buy", b: "Order" });
    const sale = abby.useFeatureFlag("sale");
    const price = abby.useRemoteConfig("price");
    return () => h("button", { onClick: onAct }, `${variant.value}:${sale.value}:${price.value}`);
  } });
  const wrapper = mount(Component, { global: { plugins: [[abby, { initialData }]] } });
  expect(wrapper.text()).toBe("Buy:true:12");
  expect(send).toHaveBeenCalledWith(expect.objectContaining({ type: AbbyEventType.PING }));
  abby.__abby__.updateLocalVariant("button", "b");
  abby.__abby__.updateFlag("sale", false);
  abby.__abby__.updateRemoteConfig("price", 21);
  await nextTick();
  expect(wrapper.text()).toBe("Order:false:21");
  await wrapper.trigger("click");
  expect(send).toHaveBeenLastCalledWith(expect.objectContaining({
    type: AbbyEventType.ACT,
    data: { projectId: "vue-test", testName: "button", selectedVariant: "b" },
  }));
  wrapper.unmount();
});

it("uses provider data without fetching and removes the provider subscription", () => {
  const abby = sdk();
  const load = vi.spyOn(abby.__abby__, "loadProjectData");
  const original = abby.__abby__.subscribe.bind(abby.__abby__);
  const stopped = vi.fn();
  vi.spyOn(abby.__abby__, "subscribe").mockImplementation((listener) => {
    const unsubscribe = original(listener);
    return () => { stopped(); unsubscribe(); };
  });
  const Child = defineComponent({ setup() {
    const flag = abby.useFeatureFlag("sale");
    return () => h("span", String(flag.value));
  } });
  const wrapper = mount(abby.AbbyProvider, { props: { initialData }, slots: { default: () => h(Child) } });
  expect(wrapper.text()).toBe("true");
  expect(load).not.toHaveBeenCalled();
  wrapper.unmount();
  expect(stopped).toHaveBeenCalledOnce();
});

it("shares one fetch/subscription across app consumers and cleans up on unmount", async () => {
  const abby = sdk();
  const load = vi.spyOn(abby.__abby__, "loadProjectData").mockImplementation(async () => abby.__abby__.init(initialData));
  const original = abby.__abby__.subscribe.bind(abby.__abby__);
  const stopped = vi.fn();
  const subscribe = vi.spyOn(abby.__abby__, "subscribe").mockImplementation((listener) => {
    const unsubscribe = original(listener);
    return () => { stopped(); unsubscribe(); };
  });
  const Child = defineComponent({ setup() {
    const price = abby.useRemoteConfig("price");
    return () => h("span", String(price.value));
  } });
  const app = createApp({ render: () => h("div", [h(Child), h(Child)]) });
  app.use(abby);
  const host = document.createElement("div");
  app.mount(host);
  await flushPromises();
  expect(host.textContent).toBe("1212");
  expect(load).toHaveBeenCalledOnce();
  expect(subscribe).toHaveBeenCalledOnce();
  app.unmount();
  expect(stopped).toHaveBeenCalledOnce();
});

it("reports load failures without an unhandled rejection", async () => {
  const abby = sdk();
  const failure = new Error("offline");
  vi.spyOn(abby.__abby__, "loadProjectData").mockRejectedValue(failure);
  const Child = defineComponent({ setup() {
    const { loading, error } = abby.useAbbyStatus();
    return () => h("span", `${loading.value}:${String(error.value)}`);
  } });
  const wrapper = mount(Child, { global: { plugins: [abby] } });
  await flushPromises();
  expect(wrapper.text()).toBe("false:Error: offline");
  wrapper.unmount();
});

it("rejects missing or different-project providers", () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const abby = sdk();
  const other = sdk();
  const Child = defineComponent({ setup() { abby.useFeatureFlag("sale"); return () => null; } });
  expect(() => mount(Child)).toThrow("same createAbby()");
  expect(() => mount(other.AbbyProvider, { props: { initialData }, slots: { default: () => h(Child) } })).toThrow("same createAbby()");
});
