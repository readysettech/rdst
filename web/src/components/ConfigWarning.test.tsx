import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfigWarning } from './ConfigWarning';
import { fetchEnvRequirements, fetchInitStatus, fetchStatus } from '../lib/api';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api');
  return {
    ...actual,
    fetchStatus: vi.fn(),
    fetchInitStatus: vi.fn(),
    fetchEnvRequirements: vi.fn(),
  };
});

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: null }),
}));

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: '/' }),
  };
});

vi.mock('./EnvSecretsDialog', () => ({
  EnvSecretsDialog: ({
    isOpen,
    onSuccess,
    requirements,
  }: {
    isOpen: boolean;
    onSuccess?: () => void;
    requirements: Array<{ accepted_names: string[] }>;
  }) =>
    isOpen ? (
      <div>
        <div data-testid="dialog-requirements">
          {requirements.map((item) => item.accepted_names[0]).join(',')}
        </div>
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Secret Save
        </button>
      </div>
    ) : null,
}));

function renderWarning(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigWarning />
    </QueryClientProvider>
  );
}

describe('ConfigWarning env secret flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [{ name: 'prod', has_password: true, is_default: true }],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: true,
      targets: [{ name: 'prod', has_password: true, is_default: true }],
      default_target: 'prod',
      llm_configured: false,
    });
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
          target: null,
          satisfied: false,
          source: 'missing',
        },
      ],
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('shows Set action when Anthropic requirement is missing', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    expect(await screen.findByRole('button', { name: /Set/i })).toBeTruthy();
    expect(screen.getByText(/export RDST_ANTHROPIC_API_KEY=<value>/i)).toBeTruthy();
  });

  it('does not show top banner for target-password-only requirements', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'target_password',
          accepted_names: ['DOCS_READYSET_PASSWORD'],
          target: 'docs_readyset',
          satisfied: false,
          source: 'missing',
        },
      ],
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Set/i })).toBeNull();
    });
  });

  it('uses Anthropic-only requirements in dialog when both are missing', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
          target: null,
          satisfied: false,
          source: 'missing',
        },
        {
          kind: 'target_password',
          accepted_names: ['DOCS_READYSET_PASSWORD'],
          target: 'docs_readyset',
          satisfied: false,
          source: 'missing',
        },
      ],
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);
    fireEvent.click(await screen.findByRole('button', { name: /Set/i }));

    const requirementsText = (await screen.findByTestId('dialog-requirements'))
      .textContent || '';
    expect(requirementsText.includes('RDST_ANTHROPIC_API_KEY')).toBe(true);
    expect(requirementsText.includes('DOCS_READYSET_PASSWORD')).toBe(false);
  });

  it('invalidates relevant queries after successful secret save', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    renderWarning(queryClient);

    fireEvent.click(await screen.findByRole('button', { name: /Set/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Mock Secret Save/i }));

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['init-status'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['env-requirements'] });
    });
  });
});
