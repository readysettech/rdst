import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

    render(
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

    const input = screen.getByPlaceholderText('Enter value for PROD_DB_PASSWORD');
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
    render(
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
  });
});
