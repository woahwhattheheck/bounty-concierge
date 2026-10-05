import { beforeEach, describe, expect, it, jest } from '@virtual/jest';

import type { ValidatedEnv } from '@/lib/backend/env';

let mockEnv: Partial<ValidatedEnv> = {};

jest.mock('@/lib/backend/env', () => ({
  getValidatedEnv: () => mockEnv,
}));

const { getSupportedConfig, RISK_PROFILES, SUPPORTED_ASSETS } = require('@/lib/backend/config');

describe('supported config overrides', () => {
  beforeEach(() => {
    mockEnv = {};
  });

  it('returns the existing supported assets and risk profiles by default', () => {
    const config = getSupportedConfig();

    expect(config.assets).toEqual(SUPPORTED_ASSETS);
    expect(config.riskProfiles).toEqual(RISK_PROFILES);
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

    mockEnv = {
      COMMITLABS_SUPPORTED_CONFIG_JSON: JSON.stringify({ assets, riskProfiles }),
    };

    const config = getSupportedConfig();

    expect(config.assets).toEqual(assets);
    expect(config.riskProfiles).toEqual(riskProfiles);
  });
});
