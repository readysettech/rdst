import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConnectionFailureActions } from './ConnectionFailureActions'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('./ProviderAllowlistPanel', () => ({
  ProviderAllowlistPanel: ({ target }: { target: string }) => (
    <div>Add my IP for {target}</div>
  ),
}))

describe('ConnectionFailureActions', () => {
  afterEach(cleanup)

  it('renders allowlist recovery and suppresses password copy for blocked IPs', () => {
    render(
      <ConnectionFailureActions
        failure={{
          target: 'supabase-db',
          message:
            'FATAL:  Address not allowed. Address is not in the allowed list',
          category: 'provider_ip_blocked_maybe',
          code: 'TARGET_PASSWORD_REQUIRED',
        }}
        passwordRequired
        onSetPassword={vi.fn()}
      />
    )

    expect(screen.getByText('Add my IP for supabase-db')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set password' })).toBeNull()
    expect(screen.queryByText(/Enter the password/i)).toBeNull()
  })
})
