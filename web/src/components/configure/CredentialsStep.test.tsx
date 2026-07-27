import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fleetStatusStub, makeTarget, renderWithClient } from '@/test-utils'
import { setEnvSecret } from '../../lib/api'
import {
  updateFleetTargetCredentials,
  useFleetStatus,
} from '../../lib/useFleet'
import type { FleetMember } from '../../types/fleet'
import { CredentialsStep } from './CredentialsStep'

vi.mock('../../lib/api', () => ({ setEnvSecret: vi.fn() }))
vi.mock('../../lib/useFleet', () => ({
  updateFleetTargetCredentials: vi.fn(),
  useFleetStatus: vi.fn(),
}))

const targets = [
  makeTarget({ name: 'orders' }),
  makeTarget({ name: 'users', engine: 'mysql', port: 3306 }),
] as unknown as FleetMember[]

function renderStep(props: Partial<Parameters<typeof CredentialsStep>[0]> = {}) {
  return renderWithClient(
    <CredentialsStep
      targets={targets}
      keyringAvailable
      onClose={vi.fn()}
      {...props}
    />
  )
}

/** Type a password into every target's field, then submit. */
function fillAndSubmit(
  container: HTMLElement,
  values: Record<string, string> = {
    orders: 'orders-secret',
    users: 'users-secret',
  }
) {
  for (const [name, value] of Object.entries(values)) {
    fireEvent.change(
      container.querySelector(`input[name="credentials-password-${name}"]`)!,
      { target: { value } }
    )
  }
  fireEvent.click(screen.getByRole('button', { name: /Save and check/ }))
}

describe('CredentialsStep', () => {
  const check = vi.fn().mockResolvedValue(undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(setEnvSecret).mockImplementation(async ({ name }) => ({
      success: true,
      name,
      persisted: true,
      session_only: false,
      message: null,
    }))
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ check }) as ReturnType<typeof useFleetStatus>
    )
  })

  afterEach(cleanup)

  it('prefills imported usernames and saves per-target passwords', async () => {
    const { container } = renderStep()

    // Imported usernames arrive pre-filled in editable inputs.
    expect(
      (container.querySelector(
        'input[name="credentials-user-orders"]'
      ) as HTMLInputElement).value
    ).toBe('orders_user')
    fillAndSubmit(container)

    await waitFor(() => expect(setEnvSecret).toHaveBeenCalledTimes(2))
    // Distinct imported env names are kept as-is.
    expect(setEnvSecret).toHaveBeenNthCalledWith(1, {
      name: 'RDST_ORDERS_PASSWORD',
      value: 'orders-secret',
      persist: true,
    })
    expect(setEnvSecret).toHaveBeenNthCalledWith(2, {
      name: 'RDST_USERS_PASSWORD',
      value: 'users-secret',
      persist: true,
    })
    expect(updateFleetTargetCredentials).not.toHaveBeenCalled()
    expect(check).toHaveBeenCalledWith(undefined, ['orders', 'users'])
  })

  it('splits a shared imported password env into per-target names', async () => {
    const sharedEnvTargets = targets.map((target) => ({
      ...target,
      password_env: 'FLEET_PASS',
    }))
    const { container } = renderStep({ targets: sharedEnvTargets })

    fillAndSubmit(container)

    await waitFor(() =>
      expect(updateFleetTargetCredentials).toHaveBeenCalledTimes(2)
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      1,
      sharedEnvTargets[0],
      'orders_user',
      'RDST_ORDERS_1_PASSWORD'
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      2,
      sharedEnvTargets[1],
      'users_user',
      'RDST_USERS_2_PASSWORD'
    )
    expect(setEnvSecret).toHaveBeenNthCalledWith(1, {
      name: 'RDST_ORDERS_1_PASSWORD',
      value: 'orders-secret',
      persist: true,
    })
    expect(setEnvSecret).toHaveBeenNthCalledWith(2, {
      name: 'RDST_USERS_2_PASSWORD',
      value: 'users-secret',
      persist: true,
    })
  })

  it('requires a password for every imported target before saving', () => {
    const { container } = renderStep()

    const save = screen.getByRole('button', {
      name: /Save and check/,
    }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.change(
      container.querySelector('input[name="credentials-password-orders"]')!,
      { target: { value: 'orders-secret' } }
    )
    expect(save.disabled).toBe(true)
    fireEvent.change(
      container.querySelector('input[name="credentials-password-users"]')!,
      { target: { value: 'users-secret' } }
    )
    expect(save.disabled).toBe(false)
  })

  it('keeps Save and check after a failed connection so the password can be retried', async () => {
    // A wrong password saves the secret fine but fails the connectivity check.
    vi.mocked(useFleetStatus).mockReturnValue({
      check,
      state: 'complete',
      results: {
        orders: {
          type: 'connectivity',
          target_name: 'orders',
          status: 'failed',
          error: 'password authentication failed',
        },
        users: { type: 'connectivity', target_name: 'users', status: 'ok' },
      },
      error: undefined,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useFleetStatus>)

    const onClose = vi.fn()
    const { container } = renderStep({ onClose })

    fillAndSubmit(container, { orders: 'wrong', users: 'users-secret' })

    await waitFor(() => expect(check).toHaveBeenCalled())
    // Retry stays available; the user is not forced into Done.
    expect(screen.getByRole('button', { name: /Save and check/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Done$/ })).toBeNull()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('offers Done only once every target connects', async () => {
    vi.mocked(useFleetStatus).mockReturnValue({
      check,
      state: 'complete',
      results: {
        orders: { type: 'connectivity', target_name: 'orders', status: 'ok' },
        users: { type: 'connectivity', target_name: 'users', status: 'ok' },
      },
      error: undefined,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useFleetStatus>)

    const onClose = vi.fn()
    const { container } = renderStep({ onClose })

    fillAndSubmit(container)

    const done = await screen.findByRole('button', { name: /^Done$/ })
    fireEvent.click(done)
    expect(onClose).toHaveBeenCalled()
  })

  it('groups cluster members under their group heading', () => {
    const clustered = targets.map((target) => ({
      ...target,
      group: 'rdst-fleet-aurora',
    }))
    renderStep({ targets: clustered })

    expect(screen.getByText('rdst-fleet-aurora')).toBeTruthy()
    expect(screen.getByText(/2 instances/)).toBeTruthy()
  })
})
