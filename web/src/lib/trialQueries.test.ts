import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { invalidateTrialRelatedQueries } from './trialQueries';

describe('invalidateTrialRelatedQueries', () => {
  it('invalidates all trial-related cache entries with full refresh', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockResolvedValue(undefined);

    await invalidateTrialRelatedQueries(queryClient);

    expect(invalidateSpy).toHaveBeenCalledTimes(5);
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
    // Trial activation promotes its email to the machine identity, so the
    // sidebar identity query is refreshed too.
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['settings', 'email'], refetchType: 'all' }),
    );
  });
});
