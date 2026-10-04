export interface IdempotencyRecord<T = unknown> {
  key: string;
  status: 'STARTED' | 'COMPLETED' | 'FAILED';
  response?: T;
  statusCode?: number;
  createdAt: number;
  expiresAt: number;
}

import type { KVStore } from './kv';

/**
 * A simple in-memory KV store with TTL support.
 * Designed to be swapped with Redis or Vercel KV.
 */
export class InMemoryKVStore implements KVStore {
  private store = new Map<string, { value: unknown; expiresAt: number }>();

  async get<T>(key: string): Promise<T | null> {
    const entry = this.store.get(key);
    if (!entry) return null;

    if (Date.now() > entry.expiresAt) {
      this.store.delete(key);
      return null;
    }

    return entry.value as T;
  }

  async set<T>(key: string, value: T, ttlSeconds: number = 3600): Promise<void> {
    this.store.set(key, {
      value,
      expiresAt: Date.now() + ttlSeconds * 1000,
    });
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async getdel<T>(key: string): Promise<T | null> {
    const value = await this.get<T>(key);
    if (value !== null) {
      this.store.delete(key);
    }
    return value;
  }

  async incr(key: string): Promise<number> {
    const value = (await this.get<number>(key)) || 0;
    const newValue = value + 1;
    await this.set(key, newValue);
    return newValue;
  }

  async expire(key: string, seconds: number): Promise<void> {
    const entry = this.store.get(key);
    if (entry) {
      entry.expiresAt = Date.now() + seconds * 1000;
    }
  }

  // Helper for cleanup (can be called periodically)
  cleanup() {
    const now = Date.now();
    for (const [key, entry] of this.store.entries()) {
      if (now > entry.expiresAt) {
        this.store.delete(key);
      }
    }
  }
}

// Keep the store and its one cleanup timer together across module re-evaluation.
// A module-local guard is reset by hot reload or vi.resetModules().
const CLEANUP_STATE_KEY = Symbol.for('commitlabs.backend.idempotency.cleanup');
interface CleanupState {
  store: InMemoryKVStore;
  intervalId: ReturnType<typeof setInterval> | null;
}
const runtime = globalThis as typeof globalThis & { [CLEANUP_STATE_KEY]?: CleanupState };
const cleanupState = (runtime[CLEANUP_STATE_KEY] ??= {
  store: new InMemoryKVStore(),
  intervalId: null,
});
const globalStore = cleanupState.store;

if (typeof setInterval !== 'undefined' && cleanupState.intervalId === null) {
  cleanupState.intervalId = setInterval(() => globalStore.cleanup(), 60 * 1000);
  // Background maintenance must not keep a Node process alive on its own.
  // Browser timers are numeric and do not provide unref().
  if (typeof cleanupState.intervalId !== 'number') {
    cleanupState.intervalId.unref?.();
  }
}

/** Stop background cleanup; a later module import may start it again. */
export function clearCleanupInterval(): void {
  if (cleanupState.intervalId !== null) {
    clearInterval(cleanupState.intervalId);
    cleanupState.intervalId = null;
  }
}

export class IdempotencyService {
  private store: KVStore;
  private ttlSeconds: number;

  constructor(store: KVStore = globalStore, ttlSeconds: number = 86400) {
    // Default 24h TTL
    this.store = store;
    this.ttlSeconds = ttlSeconds;
  }

  async getRecord<T>(key: string): Promise<IdempotencyRecord<T> | null> {
    return this.store.get<IdempotencyRecord<T>>(`idempotency:${key}`);
  }

  async start(key: string): Promise<boolean> {
    const existing = await this.getRecord(key);
    if (existing) {
      return false;
    }

    const record: IdempotencyRecord = {
      key,
      status: 'STARTED',
      createdAt: Date.now(),
      expiresAt: Date.now() + this.ttlSeconds * 1000,
    };

    await this.store.set(`idempotency:${key}`, record, this.ttlSeconds);
    return true;
  }

  async complete<T>(key: string, response: T, statusCode: number = 200): Promise<void> {
    const record: IdempotencyRecord<T> = {
      key,
      status: 'COMPLETED',
      response,
      statusCode,
      createdAt: Date.now(),
      expiresAt: Date.now() + this.ttlSeconds * 1000,
    };

    await this.store.set(`idempotency:${key}`, record, this.ttlSeconds);
  }

  async fail(key: string): Promise<void> {
    // On failure, we might want to delete the key so it can be retried,
    // or mark it as FAILED. Here we delete it to allow retries.
    await this.store.delete(`idempotency:${key}`);
  }
}

export const idempotencyService = new IdempotencyService();
