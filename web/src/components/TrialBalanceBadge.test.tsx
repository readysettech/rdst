import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  return render(
    <QueryClientProvider client={queryClient}>
      <TrialBalanceBadge />
    </QueryClientProvider>,
  );
}

describe('TrialBalanceBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows the badge when trial is active/exhausted', async () => {
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

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderBadge(queryClient);

    expect(await screen.findByText(/trial exhausted/i)).toBeTruthy();
  });

  it('hides stale trial UI when source is no longer trial', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
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

