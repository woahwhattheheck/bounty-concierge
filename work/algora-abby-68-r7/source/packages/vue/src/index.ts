import {
  Abby,
  AbbyEventType,
  HttpService,
  getABStorageKey,
  getFFStorageKey,
  getRCStorageKey,
  type ABConfig,
  type AbbyConfig,
  type AbbyDataResponse,
  type RemoteConfigValueString,
  type RemoteConfigValueStringToType,
  type StorageServiceOptions,
  type ValidatorType,
} from "@tryabby/core";
import Cookie from "js-cookie";
import {
  computed,
  defineComponent,
  inject,
  onBeforeUnmount,
  onMounted,
  provide,
  readonly,
  shallowRef,
  watch,
  type App,
  type ComputedRef,
  type InjectionKey,
  type PropType,
} from "vue";

export type ABTestReturnValue<Lookup, Variant> = Lookup extends undefined
  ? Variant
  : Variant extends keyof Lookup ? Lookup[Variant] : never;

export interface AbbyOptions {
  initialData?: AbbyDataResponse;
}

function storage(projectId: string, keyFor: (project: string, name: string) => string) {
  return {
    get: (name: string) => Cookie.get(keyFor(projectId, name)) ?? null,
    set: (name: string, value: string, options?: StorageServiceOptions) => {
      Cookie.set(keyFor(projectId, name), value, {
        expires: options?.expiresInDays ?? 365,
      });
    },
  };
}

/** Create one typed SDK per project; install it as a plugin OR use AbbyProvider. */
export function createAbby<
  const FlagName extends string,
  const Tests extends Record<string, ABConfig>,
  const RemoteConfig extends Record<string, RemoteConfigValueString>,
  const User extends Record<string, ValidatorType> = Record<string, ValidatorType>,
>(config: AbbyConfig<
  FlagName, Tests, string[], Extract<keyof RemoteConfig, string>, RemoteConfig, User
>) {
  type TestName = Extract<keyof Tests, string>;
  type ConfigName = Extract<keyof RemoteConfig, string>;
  const abby = new Abby<FlagName, TestName, Tests, RemoteConfig, ConfigName, string[], User>(
    config,
    storage(config.projectId, getABStorageKey),
    storage(config.projectId, getFFStorageKey),
    storage(config.projectId, getRCStorageKey),
  );
  type Data = ReturnType<typeof abby.getProjectData>;

  // SDK instances have distinct keys: a provider for another project cannot
  // silently satisfy a composable's injection.
  function createContext(initialData?: AbbyDataResponse) {
    const data = shallowRef<Data>(abby.getProjectData());
    const loading = shallowRef(false);
    const error = shallowRef<unknown>(null);
    let active = true;
    const unsubscribe = abby.subscribe((next) => {
      if (active) data.value = next as Data;
    });
    if (initialData) data.value = abby.init(initialData);
    let started = false;
    async function start() {
      if (started || initialData || !active) return;
      started = true;
      loading.value = true;
      try {
        const next = await abby.loadProjectData();
        if (active && next) data.value = next;
      } catch (reason) {
        if (active) error.value = reason;
      } finally {
        if (active) loading.value = false;
      }
    }
    function stop() {
      active = false;
      unsubscribe();
    }
    return { data, loading, error, start, stop };
  }
  type Context = ReturnType<typeof createContext>;
  const key: InjectionKey<Context> = Symbol("Abby");
  function useContext() {
    const context = inject(key, null);
    if (!context) {
      throw new Error("Abby composables require app.use(abby) or an AbbyProvider from the same createAbby() call.");
    }
    return context;
  }

  const AbbyProvider = defineComponent({
    name: "AbbyProvider",
    props: {
      initialData: { type: Object as PropType<AbbyDataResponse>, required: false },
    },
    setup(props, { slots }) {
      const context = createContext(props.initialData);
      provide(key, context);
      onMounted(context.start);
      onBeforeUnmount(context.stop);
      return () => slots.default?.();
    },
  });

  function install(app: App, options: AbbyOptions = {}) {
    const context = createContext(options.initialData);
    app.provide(key, context);
    app.onUnmount(context.stop);
    void context.start();
  }

  function useAbby<
    K extends TestName,
    const Lookup extends Record<Tests[K]["variants"][number], unknown> | undefined = undefined,
  >(name: K, lookup?: Lookup): {
    variant: ComputedRef<ABTestReturnValue<Lookup, Tests[K]["variants"][number]>>;
    onAct: () => void;
  } {
    const { data } = useContext();
    const selected = computed(() => data.value.tests[name]?.selectedVariant ?? "");
    const send = (type: AbbyEventType) => {
      if (!selected.value) return;
      HttpService.sendData({
        url: config.apiUrl,
        type,
        data: { projectId: config.projectId, testName: name, selectedVariant: selected.value },
      });
    };
    let stop: (() => void) | undefined;
    onMounted(() => {
      stop = watch(selected, () => send(AbbyEventType.PING), { immediate: true });
    });
    onBeforeUnmount(() => stop?.());
    const variant = computed(() => {
      const raw = selected.value as Tests[K]["variants"][number];
      return (lookup === undefined ? raw : lookup[raw]) as ABTestReturnValue<Lookup, Tests[K]["variants"][number]>;
    });
    return { variant, onAct: () => send(AbbyEventType.ACT) };
  }

  function useFeatureFlag(name: FlagName): ComputedRef<boolean> {
    const { data } = useContext();
    return computed(() => {
      // Core can omit a configured flag from a partial API response. Its getter
      // retains fallback/default behavior rather than throwing on a missing row.
      void data.value;
      return abby.getFeatureFlag(name);
    });
  }

  function useRemoteConfig<K extends ConfigName>(name: K): ComputedRef<RemoteConfigValueStringToType<RemoteConfig[K]>> {
    const { data } = useContext();
    return computed(() => {
      void data.value;
      return abby.getRemoteConfig(name);
    });
  }

  function useAbbyStatus() {
    const { loading, error } = useContext();
    return { loading: readonly(loading), error: readonly(error) };
  }

  return { install, AbbyProvider, useAbby, useFeatureFlag, useRemoteConfig, useAbbyStatus, __abby__: abby };
}
