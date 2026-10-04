import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type IdempotencyModule = typeof import('../idempotency');
const loadedModules: IdempotencyModule[] = [];

async function reloadModule(): Promise<IdempotencyModule> {
  vi.resetModules();
  const module = await import('../idempotency');
  loadedModules.push(module);
  return module;
}

describe('idempotency cleanup lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    for (const module of loadedModules) module.clearCleanupInterval();
    loadedModules.length = 0;
    Reflect.deleteProperty(globalThis, Symbol.for('commitlabs.backend.idempotency.cleanup'));
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.resetModules();
  });

  it('keeps one timer and the same records across repeated module evaluation', async () => {
    const first = await reloadModule();
    await first.idempotencyService.complete('reload-replay', { ok: true });

    for (let index = 0; index < 5; index += 1) {
      const next = await reloadModule();
      expect(vi.getTimerCount()).toBe(1);
      expect(await next.idempotencyService.getRecord('reload-replay')).toEqual(
        await first.idempotencyService.getRecord('reload-replay'),
      );
    }
  });

  it('continues cleaning the shared store once per interval after a reload', async () => {
    const first = await reloadModule();
    const cleanup = vi.spyOn(first.InMemoryKVStore.prototype, 'cleanup');
    const shortLived = new first.IdempotencyService(undefined, 1);
    await shortLived.start('expired');
    await reloadModule();

    vi.advanceTimersByTime(60_000);
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(await shortLived.getRecord('expired')).toBeNull();
    expect(vi.getTimerCount()).toBe(1);
  });

  it('lets any module copy stop cleanup and a later import restart just one timer', async () => {
    const first = await reloadModule();
    const next = await reloadModule();
    first.clearCleanupInterval();
    next.clearCleanupInterval();
    expect(vi.getTimerCount()).toBe(0);

    await reloadModule();
    expect(vi.getTimerCount()).toBe(1);
    // A retained old export still controls the current shared timer.
    first.clearCleanupInterval();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('unrefs Node timers and clears the exact handle only once', async () => {
    const handle = { unref: vi.fn() };
    const schedule = vi.fn(() => handle);
    const cancel = vi.fn();
    vi.stubGlobal('setInterval', schedule);
    vi.stubGlobal('clearInterval', cancel);
    const module = await reloadModule();
    await reloadModule();

    expect(schedule).toHaveBeenCalledTimes(1);
    expect(schedule).toHaveBeenCalledWith(expect.any(Function), 60_000);
    expect(handle.unref).toHaveBeenCalledTimes(1);
    module.clearCleanupInterval();
    module.clearCleanupInterval();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledWith(handle);
  });

  it('supports browser handles and timer shims without unref', async () => {
    for (const handle of [0, {}]) {
      const schedule = vi.fn(() => handle);
      const cancel = vi.fn();
      vi.stubGlobal('setInterval', schedule);
      vi.stubGlobal('clearInterval', cancel);
      const module = await reloadModule();
      await reloadModule();
      expect(schedule).toHaveBeenCalledTimes(1);
      module.clearCleanupInterval();
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(cancel).toHaveBeenCalledWith(handle);
    }
  });

  it('supports runtimes without a timer API', async () => {
    vi.stubGlobal('setInterval', undefined);
    const module = await reloadModule();
    await expect(module.idempotencyService.start('without-timers')).resolves.toBe(true);
    expect(() => module.clearCleanupInterval()).not.toThrow();
  });
});
