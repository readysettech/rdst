import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fleetStatusStub, makeTarget, renderWithClient } from '@/test-utils'
import {
  updateFleetTargetCredentials,
  useFleetStatus,
} from '../../lib/useFleet'
import type { FleetMember } from '../../types/fleet'
import { CredentialsStep } from './CredentialsStep'

vi.mock('../../lib/useFleet', () => ({
  updateFleetTargetCredentials: vi.fn(),
  useFleetStatus: vi.fn(),
}))

const targets = [
  makeTarget({ name: 'orders' }),
  makeTarget({ name: 'users', engine: 'mysql', port: 3306 }),
] as unknown as FleetMember[]

function renderStep(
  props: Partial<Parameters<typeof CredentialsStep>[0]> = {}
) {
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
    vi.mocked(useFleetStatus).mockReturnValue(
      fleetStatusStub({ check }) as ReturnType<typeof useFleetStatus>
    )
  })

  afterEach(cleanup)

  it('prefills imported usernames and saves per-target passwords', async () => {
    const { container } = renderStep()

    // Imported usernames arrive pre-filled in editable inputs.
    expect(
      (
        container.querySelector(
          'input[name="credentials-user-orders"]'
        ) as HTMLInputElement
      ).value
    ).toBe('orders_user')
    fillAndSubmit(container)

    await waitFor(() =>
      expect(updateFleetTargetCredentials).toHaveBeenCalledTimes(2)
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      1,
      targets[0],
      'orders_user',
      'orders-secret',
      targets[0].database,
      undefined
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      2,
      targets[1],
      'users_user',
      'users-secret',
      targets[1].database,
      undefined
    )
    expect(check).toHaveBeenCalledWith(undefined, ['orders', 'users'])
  })

  it('sends passwords without exposing shared imported environment names', async () => {
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
      'orders-secret',
      sharedEnvTargets[0].database,
      undefined
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      2,
      sharedEnvTargets[1],
      'users_user',
      'users-secret',
      sharedEnvTargets[1].database,
      undefined
    )
  })

  it('applies shared monitoring credentials to the whole batch', async () => {
    const { container } = renderStep()
    fireEvent.change(
      container.querySelector('input[name="batch-credentials-user"]')!,
      { target: { value: 'monitoring' } }
    )
    fireEvent.change(
      container.querySelector('input[name="batch-credentials-password"]')!,
      { target: { value: 'shared-secret' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Apply to all' }))

    for (const target of targets) {
      expect(
        (
          container.querySelector(
            `input[name="credentials-user-${target.name}"]`
          ) as HTMLInputElement
        ).value
      ).toBe('monitoring')
      expect(
        (
          container.querySelector(
            `input[name="credentials-password-${target.name}"]`
          ) as HTMLInputElement
        ).value
      ).toBe('shared-secret')
    }
  })

  it('uses an imported Secrets Manager ARN without asking for a password', async () => {
    const secretTarget = {
      ...targets[0],
      password_secret_arn: 'arn:aws:secretsmanager:us-east-1:123:secret:orders',
      password_secret_key: 'password',
    }
    const { container } = renderStep({ targets: [secretTarget] })

    expect(
      screen.getByText('Credentials stored in AWS Secrets Manager')
    ).toBeTruthy()
    expect(
      container.querySelector('input[name="credentials-password-orders"]')
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Save and check/ }))

    await waitFor(() =>
      expect(check).toHaveBeenCalledWith(undefined, ['orders'])
    )
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

  it('explains that passwords are session-only without a secure keychain', () => {
    renderStep({ keyringAvailable: false })

    expect(
      screen.getByText(
        /No OS keychain found\. Re-enter the password after RDST restarts\./i
      )
    ).toBeTruthy()
    expect(screen.queryByText(/config\.toml/i)).toBeNull()
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

  it('shows writable integration guidance without blocking finish', async () => {
    vi.mocked(useFleetStatus).mockReturnValue({
      check,
      state: 'complete',
      results: {
        orders: {
          type: 'connectivity',
          target_name: 'orders',
          status: 'ok',
          privileges: { writable: true, evidence: 'INSERT grant found.' },
        },
      },
      error: undefined,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useFleetStatus>)

    const { container } = renderStep({ targets: [targets[0]] })

    expect(screen.getByText('Read-only access highly recommended')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Proceed anyway' })).toBeNull()
    fireEvent.click(
      screen.getByRole('button', { name: 'View read-only setup' })
    )
    expect(
      screen.getByText('Administrator SQL for a read-only user')
    ).toBeTruthy()
    const sql = document.querySelector('.cm-content')?.textContent ?? ''
    expect(sql).toContain('CREATE ROLE rdst_readonly')
    expect(sql).toContain('NOBYPASSRLS')
    expect(sql).toContain('CONNECTION LIMIT 20')
    expect(sql).toContain('default_transaction_read_only = on')
    expect(sql).not.toContain('statement_timeout')
    expect(sql).toContain('GRANT CONNECT ON DATABASE "orders"')
    expect(sql).toContain('GRANT USAGE ON SCHEMA public')
    expect(sql).toContain('GRANT SELECT ON ALL TABLES')
    expect(sql).toContain('GRANT pg_read_all_stats')
    expect(sql).not.toContain('ALTER DEFAULT PRIVILEGES')

    fillAndSubmit(container, { orders: 'orders-secret' })
    expect(await screen.findByRole('button', { name: /^Done$/ })).toBeTruthy()
  })

  it('renders copy-pasteable MySQL read-only and diagnostics grants', () => {
    vi.mocked(useFleetStatus).mockReturnValue({
      check,
      state: 'complete',
      results: {
        users: {
          type: 'connectivity',
          target_name: 'users',
          status: 'ok',
          privileges: { writable: true, evidence: 'INSERT grant found.' },
        },
      },
      error: undefined,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useFleetStatus>)

    renderStep({ targets: [targets[1]] })

    fireEvent.click(
      screen.getByRole('button', { name: 'View read-only setup' })
    )
    const sql = document.querySelector('.cm-content')?.textContent ?? ''
    expect(sql).toContain(
      "CREATE USER 'rdst_readonly'@'replace_with_rdst_client_host'"
    )
    expect(sql).toContain('MAX_USER_CONNECTIONS 20')
    expect(sql).toContain('GRANT SELECT, SHOW VIEW ON `users`.*')
    expect(sql).toContain('GRANT PROCESS ON *.*')
    expect(sql).toContain('performance_schema.*')
    expect(sql).toContain('GRANT REPLICATION CLIENT ON *.*')
    expect(sql).toContain('GRANT SELECT ON mysql.slow_log')
  })

  it('shows inline guidance for every writable integration target', () => {
    vi.mocked(useFleetStatus).mockReturnValue({
      check,
      state: 'complete',
      results: {
        orders: {
          type: 'connectivity',
          target_name: 'orders',
          status: 'ok',
          privileges: { writable: true, evidence: 'INSERT grant found.' },
        },
        users: {
          type: 'connectivity',
          target_name: 'users',
          status: 'ok',
          privileges: { writable: true, evidence: 'INSERT grant found.' },
        },
      },
      error: undefined,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useFleetStatus>)

    renderStep()

    expect(
      screen.getByText(
        'Read-only access is recommended for 2 connected accounts.'
      )
    ).toBeTruthy()
    expect(document.querySelectorAll('.cm-content')).toHaveLength(0)
    screen
      .getAllByRole('button', { name: 'View read-only setup' })
      .forEach((button) => fireEvent.click(button))
    const sqlBlocks = document.querySelectorAll('.cm-content')
    expect(sqlBlocks).toHaveLength(2)
    expect(sqlBlocks[0]?.textContent).toContain('CREATE ROLE rdst_readonly')
    expect(sqlBlocks[1]?.textContent).toContain("CREATE USER 'rdst_readonly'")
    expect(screen.queryByRole('button', { name: 'Proceed anyway' })).toBeNull()
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

  it('assembles independent per-target SSH payloads and prefills the next target', async () => {
    const { container } = renderStep({
      privateTargetGroups: [
        {
          key: 'vpc-1',
          label: 'Private VPC',
          targetNames: ['orders', 'users'],
        },
      ],
    })

    const ordersHost = container.querySelector(
      'input[name="private-orders-host"]'
    ) as HTMLInputElement
    const usersHost = container.querySelector(
      'input[name="private-users-host"]'
    ) as HTMLInputElement
    fireEvent.change(ordersHost, { target: { value: 'jump.shared.test' } })
    expect(usersHost.value).toBe('jump.shared.test')

    fireEvent.change(usersHost, { target: { value: 'jump.users.test' } })
    fillAndSubmit(container)

    await waitFor(() =>
      expect(updateFleetTargetCredentials).toHaveBeenCalledTimes(2)
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      1,
      targets[0],
      'orders_user',
      'orders-secret',
      targets[0].database,
      expect.objectContaining({
        host: 'jump.shared.test',
        port: 22,
      })
    )
    expect(updateFleetTargetCredentials).toHaveBeenNthCalledWith(
      2,
      targets[1],
      'users_user',
      'users-secret',
      targets[1].database,
      expect.objectContaining({
        host: 'jump.users.test',
        port: 22,
      })
    )
  })

  it('requires a database name when discovery did not provide one', () => {
    const mysqlWithoutDatabase = [
      {
        ...targets[1],
        database: '',
        tags: ['aws-account:111122223333'],
      },
    ]
    const { container } = renderStep({ targets: mysqlWithoutDatabase })
    expect(
      screen.getByText('Enter the database name used by this instance.')
    ).toBeTruthy()
    fireEvent.change(
      container.querySelector('input[name="credentials-password-users"]')!,
      { target: { value: 'users-secret' } }
    )

    const save = screen.getByRole('button', {
      name: /Save and check/,
    }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.change(
      container.querySelector('input[name="credentials-database-users"]')!,
      { target: { value: 'app' } }
    )
    expect(save.disabled).toBe(false)
  })
})
