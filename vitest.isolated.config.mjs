import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    environment: 'node',
    include: [
      'src/lib/backend/__tests__/idempotency.test.ts',
      'src/lib/backend/__tests__/idempotency.cleanup.test.ts',
    ],
    maxWorkers: 1,
    minWorkers: 1,
    coverage: {
      provider: 'v8',
      include: ['src/lib/backend/idempotency.ts'],
      reporter: ['text', 'json', 'json-summary'],
    },
  },
});
