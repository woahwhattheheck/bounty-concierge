import { beforeEach, describe, expect, it, vi } from 'vitest';

const envState = vi.hoisted(() => ({
  current: {} as Record<string, string | undefined>,
}));

vi.mock('@/lib/backend/env', () => ({
  getValidatedEnv: () => envState.current,
}));

import {
  getSupportedConfig,
  PARAMETER_BOUNDS,
  RISK_PROFILES,
  SUPPORTED_ASSETS,
} from '@/lib/backend/config';

describe('supported config overrides', () => {
  beforeEach(() => {
    envState.current = {};
  });

  it('returns the existing supported assets and risk profiles by default', () => {
    expect(getSupportedConfig()).toEqual({
      assets: SUPPORTED_ASSETS,
      riskProfiles: RISK_PROFILES,
      bounds: PARAMETER_BOUNDS,
    });
  });

  it('uses COMMITLABS_SUPPORTED_CONFIG_JSON assets and risk profiles when provided', () => {
    const assets = [{ code: 'EURC', name: 'Euro Coin', decimals: 7 }];
    const riskProfiles = [
      {
        id: 'moderate',
        name: 'Moderate',
        description: 'Custom deployment threshold',
        maxLossBps: 2500,
        lockDurationDays: 45,
      },
    ];

    envState.current = {
      COMMITLABS_SUPPORTED_CONFIG_JSON: JSON.stringify({ assets, riskProfiles }),
    };

    expect(getSupportedConfig()).toEqual({
      assets,
      riskProfiles,
      bounds: PARAMETER_BOUNDS,
    });
  });
});
