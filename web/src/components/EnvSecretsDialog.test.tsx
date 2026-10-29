import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTestQueryClient, renderWithClient } from '@/test-utils'
import { setEnvSecret } from '../lib/api'
import { EnvSecretsDialog } from './EnvSecretsDialog'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    setEnvSecret: vi.fn(),
  }
})

/**
 * The dialog under an exhausted trial: the one requirement shape the rerender
 * tests below share, so each frame only re-states its `isOpen`.
 */
function exhaustedDialog(onClose: () => void, isOpen = true) {
  return (
    <EnvSecretsDialog
      isOpen={isOpen}
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
  )
}

describe('EnvSecretsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('is the quiet dialog: one title, the masking note in the footer, a plain save', () => {
    renderWithClient(exhaustedDialog(vi.fn()))

    expect(
      screen.getByRole('heading', { name: 'Update Anthropic API key' })
    ).toBeTruthy()
    expect(
      screen.getByText('Secrets are masked and never shown after submission.')
    ).toBeTruthy()
    const save = screen.getByRole('button', { name: 'Save secrets' })
    expect(save.querySelector('svg')).toBeNull()
    expect(screen.getByLabelText('Anthropic API key')).toBeTruthy()
  })

  it('renders missing requirements and submits masked values', async () => {
    const onSuccess = vi.fn()
    const onClose = vi.fn()

    vi.mocked(setEnvSecret).mockResolvedValue({
      success: true,
      name: 'PROD_DB_PASSWORD',
      persisted: true,
      session_only: false,
    })

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
    )

    const input = screen.getByPlaceholderText('Enter Password (prod)')
    expect((input as HTMLInputElement).type).toBe('text')
    expect((input as HTMLInputElement).autocomplete).toBe('off')
    expect(input.getAttribute('data-1p-ignore')).toBe('true')
    expect(input.getAttribute('data-lpignore')).toBe('true')
    expect(
      screen
        .getByRole('switch', { name: 'Save securely' })
        .getAttribute('aria-checked')
    ).toBe('true')
    fireEvent.change(input, { target: { value: 'my-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))

    await waitFor(() => {
      expect(setEnvSecret).toHaveBeenCalledWith({
        name: 'PROD_DB_PASSWORD',
        value: 'my-secret',
        persist: true,
      })
    })
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('explains session-only target password storage when keyring is unavailable', () => {
    renderWithClient(
      <EnvSecretsDialog
        isOpen
        onClose={() => {}}
        keyringAvailable={false}
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
    )

    expect(
      screen.getByText(
        /No OS keychain found\. Re-enter the password after RDST restarts\./i
      )
    ).toBeTruthy()
    expect(screen.getByLabelText('Password (prod)')).toBeTruthy()
    expect(screen.queryByText(/config\.toml/i)).toBeNull()
    expect(screen.queryByText(/Save between restarts/i)).toBeNull()
  })

  it('warns about a startup key only when RDST was started with one', () => {
    const dialog = (startup: boolean) => (
      <EnvSecretsDialog
        isOpen
        onClose={vi.fn()}
        keyringAvailable
        showManualAnthropicInput
        requirements={[
          {
            kind: 'anthropic_api_key',
            accepted_names: ['ANTHROPIC_API_KEY'],
            target: null,
            satisfied: true,
            // A key saved in Settings also lives in the process environment.
            source: 'process_env',
            anthropic_key_in_startup_env: startup,
          },
        ]}
      />
    )
    const warning =
      /RDST was started with an ANTHROPIC_API_KEY environment variable/

    const { rerender } = renderWithClient(dialog(false))
    expect(screen.queryByText(warning)).toBeNull()

    rerender(dialog(true))
    expect(screen.getByText(warning)).toBeTruthy()
  })

  it('keeps the dialog open with the reason when the key is not saved', async () => {
    const onClose = vi.fn()
    const onSuccess = vi.fn()
    vi.mocked(setEnvSecret).mockResolvedValue({
      success: false,
      name: 'ANTHROPIC_API_KEY',
      persisted: false,
      session_only: true,
      message:
        "Anthropic didn't accept this key, so it wasn't saved. Copy the key from the Anthropic Console and try again. Your current key is still in use.",
    })
    renderWithClient(
      <EnvSecretsDialog
        isOpen
        onClose={onClose}
        onSuccess={onSuccess}
        keyringAvailable
        showManualAnthropicInput
        requirements={[]}
      />
    )

    fireEvent.change(screen.getByPlaceholderText('Enter Anthropic API key'), {
      target: { value: 'sk-ant-not-a-real-key' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))

    expect(
      await screen.findByText(/Your current key is still in use\./)
    ).toBeTruthy()
    expect(onSuccess).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('rejects an Anthropic key with characters outside the key alphabet before saving', () => {
    const onClose = vi.fn()
    renderWithClient(exhaustedDialog(onClose))

    fireEvent.change(screen.getByPlaceholderText('Enter Anthropic API key'), {
      target: { value: 'sk-ant-\u00e9\u6f22' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))

    expect(screen.getByText(/isn't a valid Anthropic API key/i)).toBeTruthy()
    expect(setEnvSecret).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('preserves typed values and validation state when parent rerenders while open', async () => {
    const onClose = vi.fn()
    const queryClient = createTestQueryClient()

    const { rerender } = renderWithClient(exhaustedDialog(onClose), queryClient)

    const input = screen.getByPlaceholderText('Enter Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-typed' } })
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))

    rerender(exhaustedDialog(onClose))

    expect(
      (
        screen.getByPlaceholderText(
          'Enter Anthropic API key'
        ) as HTMLInputElement
      ).value
    ).toBe('sk-ant-typed')
  })

  it('preserves validation state when parent rerenders while open', () => {
    const onClose = vi.fn()
    const queryClient = createTestQueryClient()

    const { rerender } = renderWithClient(exhaustedDialog(onClose), queryClient)

    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))
    expect(
      screen.getByText(/Enter at least one secret value before saving\./i)
    ).toBeTruthy()

    rerender(exhaustedDialog(onClose))

    expect(
      screen.getByText(/Enter at least one secret value before saving\./i)
    ).toBeTruthy()
  })

  it('resets form state after close and reopen', () => {
    const onClose = vi.fn()
    const queryClient = createTestQueryClient()

    const { rerender } = renderWithClient(exhaustedDialog(onClose), queryClient)

    const input = screen.getByPlaceholderText('Enter Anthropic API key')
    fireEvent.change(input, { target: { value: 'sk-ant-typed' } })
    fireEvent.click(screen.getByRole('button', { name: /Save secrets/i }))

    rerender(exhaustedDialog(onClose, false))

    rerender(exhaustedDialog(onClose))

    expect(
      (
        screen.getByPlaceholderText(
          'Enter Anthropic API key'
        ) as HTMLInputElement
      ).value
    ).toBe('')
    expect(
      screen.queryByText(/Enter at least one secret value before saving\./i)
    ).toBeNull()
  })
})
