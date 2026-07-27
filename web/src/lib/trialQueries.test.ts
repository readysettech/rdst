import { describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test-utils';

import {
  invalidateAiGateQueries,
  invalidateTrialRelatedQueries,
} from './trialQueries';

describe('invalidateAiGateQueries', () => {
  it('drops the cached key verdict along with the requirements', async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockResolvedValue(undefined);

    await invalidateAiGateQueries(queryClient);

    expect(invalidateSpy).toHaveBeenCalledTimes(3);
    for (const queryKey of [
      ['env-requirements'],
      ['anthropic-validity'],
      ['trial-status'],
    ]) {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey, refetchType: 'all' }),
      );
    }
  });
});

describe('invalidateTrialRelatedQueries', () => {
  it('invalidates all trial-related cache entries with full refresh', async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockResolvedValue(undefined);

    await invalidateTrialRelatedQueries(queryClient);

    expect(invalidateSpy).toHaveBeenCalledTimes(6);
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['status'], refetchType: 'all' }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['init-status'], refetchType: 'all' }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['env-requirements'], refetchType: 'all' }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['trial-status'], refetchType: 'all' }),
    );
    // A saved key or token makes any cached verdict about the previous one
    // meaningless; the gate must re-probe instead of reading the stale answer.
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['anthropic-validity'],
        refetchType: 'all',
      }),
    );
    // Trial activation promotes its email to the machine identity, so the
    // sidebar identity query is refreshed too.
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['settings', 'email'], refetchType: 'all' }),
    );
  });
});
