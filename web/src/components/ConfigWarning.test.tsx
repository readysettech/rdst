import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfigWarning } from './ConfigWarning';
import {
  fetchEnvRequirements,
  fetchInitStatus,
  fetchStatus,
  fetchTrialStatus,
} from '../lib/api';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api');
  return {
    ...actual,
    fetchStatus: vi.fn(),
    fetchInitStatus: vi.fn(),
    fetchEnvRequirements: vi.fn(),
    fetchTrialStatus: vi.fn(),
  };
});

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: null }),
}));

// The banner shows on feature pages and stays off the home page, so the
// default mocked route is a feature page; tests flip mockPathname to '/'
// to cover the home suppression.
let mockPathname = '/ask';
const mockNavigate = vi.fn();

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => ({ pathname: mockPathname }),
  };
});

vi.mock('./EnvSecretsDialog', () => ({
  EnvSecretsDialog: ({
    isOpen,
    onSuccess,
    requirements,
    showManualAnthropicInput = false,
  }: {
    isOpen: boolean;
    onSuccess?: () => void;
    requirements: Array<{ accepted_names: string[] }>;
    showManualAnthropicInput?: boolean;
  }) =>
    isOpen ? (
      <div>
        <div data-testid="dialog-requirements">
          {(requirements.length > 0 || !showManualAnthropicInput
            ? requirements
            : [{ accepted_names: ['ANTHROPIC_API_KEY'] }]
          ).map((item) => item.accepted_names[0]).join(',')}
        </div>
        {(requirements.length > 0 || !showManualAnthropicInput ? requirements : [{ accepted_names: ['ANTHROPIC_API_KEY'] }]).map((item) => (
          <label key={item.accepted_names[0]}>
            {`Secret ${item.accepted_names[0]}`}
            <input
              aria-label={`Secret ${item.accepted_names[0]}`}
              onChange={() => undefined}
            />
          </label>
        ))}
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Secret Save
        </button>
      </div>
    ) : null,
}));

vi.mock('./TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: ({
    isOpen,
    onSuccess,
  }: {
    isOpen: boolean;
    onSuccess?: () => void;
  }) =>
    isOpen ? (
      <div data-testid="trial-dialog">
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Trial Success
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
    mockPathname = '/ask';
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [{ name: 'prod', has_password: true, is_default: true }],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: true,
      targets: [{ name: 'prod', engine: 'postgresql', has_password: true, is_default: true }],
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

  it('shows trial and key actions when Anthropic requirement is missing', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    expect(await screen.findByRole('button', { name: /Claim free trial credits/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Set API Key/i })).toBeTruthy();
    // Env-var plumbing stays out of the UI: no export command, no raw names.
    expect(screen.queryByText(/ANTHROPIC_API_KEY/)).toBeNull();
  });

  it('stays off the demo page even when the key is missing', async () => {
    mockPathname = '/demo';
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    await waitFor(() => expect(vi.mocked(fetchEnvRequirements)).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /Claim free trial credits/i })).toBeNull();
    expect(screen.queryByText(/Missing Anthropic API Key/i)).toBeNull();
  });

  it('stays off the home page even when the key is missing', async () => {
    mockPathname = '/';
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    await waitFor(() => expect(vi.mocked(fetchEnvRequirements)).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /Claim free trial credits/i })).toBeNull();
    expect(screen.queryByText(/Missing Anthropic API Key/i)).toBeNull();
  });

  it('keeps a fresh zero-target install on the home page', async () => {
    mockPathname = '/';
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: false,
      targets: [],
      default_target: null,
      llm_configured: false,
    });

    renderWarning(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    await waitFor(() => expect(vi.mocked(fetchStatus)).toHaveBeenCalled());
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('allows a fresh zero-target install to open the demo', async () => {
    mockPathname = '/demo';
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: false,
      targets: [],
      default_target: null,
      llm_configured: false,
    });

    renderWarning(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    await waitFor(() => expect(vi.mocked(fetchStatus)).toHaveBeenCalled());
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('routes a zero-target database feature to onboarding', async () => {
    mockPathname = '/analyze';
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: false,
      targets: [],
      default_target: null,
      llm_configured: false,
    });

    renderWarning(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    await waitFor(() => {
      // Routes to Connect preserving the intended destination so the user
      // returns to /analyze after connecting (route-with-intent).
      expect(mockNavigate).toHaveBeenCalledWith({
        to: '/onboarding',
        search: { redirect: '/analyze' },
      });
    });
  });

  it('keeps a zero-target install on settings instead of onboarding', async () => {
    mockPathname = '/configure';
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    });
    vi.mocked(fetchInitStatus).mockResolvedValue({
      initialized: false,
      targets: [],
      default_target: null,
      llm_configured: false,
    });

    renderWarning(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    await waitFor(() => expect(vi.mocked(fetchStatus)).toHaveBeenCalled());
    // Settings stays reachable so keys and the reset control are usable.
    expect(mockNavigate).not.toHaveBeenCalled();
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
      expect(screen.queryByRole('button', { name: /Claim free trial credits/i })).toBeNull();
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
    fireEvent.click(await screen.findByRole('button', { name: /Set API Key/i }));

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

    fireEvent.click(await screen.findByRole('button', { name: /Set API Key/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Mock Secret Save/i }));

    await waitFor(() => {
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
    });
  });

  it('always shows Anthropic API key input for exhausted-trial banner even with no missing requirement entries', async () => {
    vi.mocked(fetchTrialStatus).mockResolvedValue({
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
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
          target: null,
          satisfied: true,
          source: 'trial_exhausted',
        },
      ],
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    renderWarning(queryClient);

    fireEvent.click(await screen.findByRole('button', { name: /Set API Key/i }));
    const keyInput = await screen.findByLabelText(/secret anthropic_api_key/i);
    fireEvent.change(keyInput, { target: { value: 'sk-ant-manual-token' } });
    expect((keyInput as HTMLInputElement).value).toBe('sk-ant-manual-token');
  });

  it('does not show a trial warning when source is no longer trial even if trial status cache is exhausted', async () => {
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
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
          target: null,
          satisfied: true,
          source: 'process_env',
        },
      ],
    });

    renderWarning(queryClient);

    await waitFor(() => {
      expect(screen.queryByText(/Trial Credits Exhausted/i)).toBeNull();
    });
  });
});
