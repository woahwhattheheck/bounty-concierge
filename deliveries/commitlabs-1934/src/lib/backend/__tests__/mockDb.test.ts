import { beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'fs/promises';
import { getMockData } from '../mockDb';
import { logError } from '../logger';

vi.mock('fs/promises', () => ({ default: { readFile: vi.fn() } }));
vi.mock('../logger', () => ({ logError: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe('getMockData read failures', () => {
  it('returns fresh empty data only when the backing file is missing', async () => {
    vi.mocked(fs.readFile).mockRejectedValue(Object.assign(new Error('missing'), { code: 'ENOENT' }));
    const first = await getMockData();
    expect(first).toEqual({ commitments: [], attestations: [], listings: [] });
    first.commitments.push({ id: 'not-persisted' });
    expect(await getMockData()).toEqual({ commitments: [], attestations: [], listings: [] });
    expect(logError).not.toHaveBeenCalled();
  });

  it('logs and rejects malformed JSON instead of returning empty data', async () => {
    vi.mocked(fs.readFile).mockResolvedValue('{"commitments":');
    await expect(getMockData()).rejects.toBeInstanceOf(SyntaxError);
    expect(logError).toHaveBeenCalledWith(undefined, 'Failed to read mock database');
  });

  it('logs and preserves a permission error instead of returning empty data', async () => {
    const error = Object.assign(new Error('permission denied'), { code: 'EACCES' });
    vi.mocked(fs.readFile).mockRejectedValue(error);
    await expect(getMockData()).rejects.toBe(error);
    expect(logError).toHaveBeenCalledWith(undefined, 'Failed to read mock database');
  });

  it('retains valid records and normalizes omitted collections', async () => {
    vi.mocked(fs.readFile).mockResolvedValue('{"commitments":[{"id":"retained"}]}');
    expect(await getMockData()).toEqual({
      commitments: [{ id: 'retained' }], attestations: [], listings: [],
    });
    expect(logError).not.toHaveBeenCalled();
  });
});
