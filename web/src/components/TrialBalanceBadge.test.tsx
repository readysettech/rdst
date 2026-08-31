import type { QueryClient } from '@tanstack/react-query';
import { cleanup, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient, renderWithClient } from '@/test-utils';

import { TrialBalanceBadge } from './TrialBalanceBadge';
import { fetchEnvRequirements, fetchTrialStatus } from '../lib/api';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api');
  return {
    ...actual,
    fetchEnvRequirements: vi.fn(),
    fetchTrialStatus: vi.fn(),
  };
});

function renderBadge(queryClient: QueryClient) {
  return renderWithClient(<TrialBalanceBadge />, queryClient);
}

describe('TrialBalanceBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('does not show the removed trial UI for an active legacy token', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY'],
          target: null,
          satisfied: true,
          source: 'trial',
        },
      ],
    });
    vi.mocked(fetchTrialStatus).mockResolvedValue({
      active: true,
      status: 'active',
      percent_remaining: 60,
      remaining_tokens_display: '60',
      limit_tokens_display: '100',
    });

    const queryClient = createTestQueryClient();

    renderBadge(queryClient);

    expect(screen.queryByText(/free trial/i)).toBeNull();
    expect(screen.queryByText('60/100')).toBeNull();
  });

  it('does not show the removed trial UI for an exhausted legacy token', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY'],
          target: null,
          satisfied: true,
          source: 'trial_exhausted',
        },
      ],
    });
    vi.mocked(fetchTrialStatus).mockResolvedValue({
      active: false,
      status: 'exhausted',
      percent_remaining: 0,
      remaining_tokens_display: '0',
      limit_tokens_display: '100',
    });

    const queryClient = createTestQueryClient();

    renderBadge(queryClient);

    expect(screen.queryByText(/trial exhausted/i)).toBeNull();
  });

  it('hides stale trial UI when source is no longer trial', () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(['trial-status'], {
      active: false,
      status: 'exhausted',
      percent_remaining: 0,
      remaining_tokens_display: '0',
      limit_tokens_display: '100',
    });

    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY'],
          target: null,
          satisfied: true,
          source: 'process_env',
        },
      ],
    });
    vi.mocked(fetchTrialStatus).mockResolvedValue({
      active: false,
      status: 'active',
      percent_remaining: 0,
      remaining_tokens_display: '0',
      limit_tokens_display: '100',
    });

    renderBadge(queryClient);

    expect(screen.queryByText(/trial exhausted/i)).toBeNull();
    expect(screen.queryByText(/free trial/i)).toBeNull();
  });
});
