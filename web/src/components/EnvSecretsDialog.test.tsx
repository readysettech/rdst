import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { EnvSecretsDialog } from './EnvSecretsDialog';
import { setEnvSecret } from '../lib/api';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api');
  return {
    ...actual,
    setEnvSecret: vi.fn(),
  };
});

describe('EnvSecretsDialog', () => {
  const renderWithClient = (ui: ReactElement) => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders missing requirements and submits masked values', async () => {
    const onSuccess = vi.fn();
    const onClose = vi.fn();

    vi.mocked(setEnvSecret).mockResolvedValue({
      success: true,
      name: 'PROD_DB_PASSWORD',
      persisted: true,
      session_only: false,
    });

    renderWithClient(
      <EnvSecretsDialog
        isOpen
        onClose={onClose}
        onSuccess={onSuccess}
        keyringAvailable
        requirements={[
          {
            kind: 'target_password',
            accepted_names: ['PROD_DB_PASSWORD'],
            target: 'prod',
            satisfied: false,
            source: 'missing',
          },
        ]}
      />
    );

    const input = screen.getByPlaceholderText('Enter Password (prod)');
    expect((input as HTMLInputElement).type).toBe('text');
    expect((input as HTMLInputElement).autocomplete).toBe('off');
    expect(input.getAttribute('data-1p-ignore')).toBe('true');
    expect(input.getAttribute('data-lpignore')).toBe('true');
    fireEvent.change(input, { target: { value: 'my-secret' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Secrets/i }));

    await waitFor(() => {
      expect(setEnvSecret).toHaveBeenCalledWith({
        name: 'PROD_DB_PASSWORD',
        value: 'my-secret',
        persist: true,
      });
    });
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows session-only warning when keyring is unavailable', () => {
    renderWithClient(
      <EnvSecretsDialog
        isOpen
        onClose={() => {}}
        keyringAvailable={false}
        requirements={[
          {
            kind: 'anthropic_api_key',
            accepted_names: ['RDST_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
            target: null,
            satisfied: false,
            source: 'missing',
          },
        ]}
      />
    );

    expect(
      screen.getByText(/Secure keychain is unavailable\. Values will be session-only\./i)
    ).toBeTruthy();
    expect(screen.getByLabelText('Anthropic API Key')).toBeTruthy();
  });

  it('preserves typed values and validation state when parent rerenders while open', async () => {
    const onClose = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    const input = screen.getByPlaceholderText('Enter Anthropic API Key');
    fireEvent.change(input, { target: { value: 'sk-ant-typed' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Secrets/i }));

    rerender(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    expect((screen.getByPlaceholderText('Enter Anthropic API Key') as HTMLInputElement).value).toBe(
      'sk-ant-typed'
    );
  });

  it('preserves validation state when parent rerenders while open', () => {
    const onClose = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /Save Secrets/i }));
    expect(screen.getByText(/Enter at least one secret value before saving\./i)).toBeTruthy();

    rerender(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    expect(screen.getByText(/Enter at least one secret value before saving\./i)).toBeTruthy();
  });

  it('resets form state after close and reopen', () => {
    const onClose = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    const input = screen.getByPlaceholderText('Enter Anthropic API Key');
    fireEvent.change(input, { target: { value: 'sk-ant-typed' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Secrets/i }));

    rerender(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen={false}
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    rerender(
      <QueryClientProvider client={queryClient}>
        <EnvSecretsDialog
          isOpen
          onClose={onClose}
          keyringAvailable
          showManualAnthropicInput
          requirements={[
            {
              kind: 'anthropic_api_key',
              accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
              target: null,
              satisfied: true,
              source: 'trial_exhausted',
            },
          ]}
        />
      </QueryClientProvider>
    );

    expect((screen.getByPlaceholderText('Enter Anthropic API Key') as HTMLInputElement).value).toBe('');
    expect(screen.queryByText(/Enter at least one secret value before saving\./i)).toBeNull();
  });
});
