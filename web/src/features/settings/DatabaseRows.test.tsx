import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FleetConnectivityEvent } from '../../types/fleet'
import { DatabaseRows } from './DatabaseRows'

vi.mock('../../components/TargetMonitorStatus', () => ({
  useTargetMonitorLines: () =>
    new Map([
      ['orders', { dot: 'live', tone: 'positive', monitored: true, ai: true }],
      [
        'reports',
        { dot: 'hollow', tone: 'neutral', monitored: false, ai: false },
      ],
    ]),
}))

const signIn = vi.hoisted(() => ({ held: new Set<string>() }))

vi.mock('../monitor-kit/signInHold', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../monitor-kit/signInHold')>()),
  useSignInHeld: () => signIn.held,
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: { to: string; children?: ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

const targets = [
  {
    name: 'orders',
    engine: 'postgresql',
    host: 'orders.test',
    port: 5432,
    has_password: true,
    is_default: true,
  },
  {
    name: 'reports',
    engine: 'mysql',
    host: 'reports.test',
    port: 3306,
    has_password: false,
    is_default: false,
  },
]

const failure: FleetConnectivityEvent = {
  type: 'connectivity',
  target_name: 'reports',
  status: 'failed',
  error: 'password authentication failed',
  code: 'TARGET_PASSWORD_REQUIRED',
  latency_ms: null,
  server_version: null,
}

function renderRows(over: Partial<Parameters<typeof DatabaseRows>[0]> = {}) {
  const props = {
    targets,
    pending: false,
    connectivity: { reports: failure },
    testingName: null,
    onTest: vi.fn(),
    onEdit: vi.fn(),
    onSetDefault: vi.fn(),
    onRemove: vi.fn(),
    onSetPassword: vi.fn(),
    onTestTunnel: vi.fn(),
    ...over,
  }
  render(<DatabaseRows {...props} />)
  return props
}

afterEach(() => {
  cleanup()
  signIn.held = new Set()
})

describe('DatabaseRows', () => {
  it('reads each database as name, connection, state word and its actions', () => {
    const props = renderRows()
    expect(screen.getByText('PostgreSQL at orders.test:5432')).toBeTruthy()
    expect(screen.getByText('default').title).toBe(
      'Used when no database is chosen'
    )
    expect(screen.getByText('Watching')).toBeTruthy()
    expect(screen.getByText('Not monitored')).toBeTruthy()
    expect(screen.getByText('Password needed')).toBeTruthy()
    expect(screen.getByText('password authentication failed')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Set password' }))
    expect(props.onSetPassword).toHaveBeenCalledWith(targets[1])
    fireEvent.click(
      screen.getAllByRole('button', { name: 'Test connection' })[0]
    )
    expect(props.onTest).toHaveBeenCalledWith('orders')
    expect(
      screen
        .getAllByRole('button')
        .filter((button) =>
          button.className.includes('bg-surface-primary-solid')
        )
    ).toEqual([])
  })

  it('removes a database only after the confirm', () => {
    const props = renderRows()
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'More actions for reports' }),
      { key: 'Enter' }
    )
    expect(
      screen.getAllByRole('menuitem').map((item) => item.textContent)
    ).toEqual(['Set default', 'Edit', 'Remove'])
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove' }))
    expect(screen.getByText('Remove reports?')).toBeTruthy()
    expect(
      screen.getByText(
        'RDST forgets the connection and deletes its readings, Inbox items, actions and reports. Adding it back starts fresh.'
      )
    ).toBeTruthy()
    expect(props.onRemove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(props.onRemove).toHaveBeenCalledWith('reports')
  })

  it('says a row tested here connected, like the edit dialog', () => {
    const ok: FleetConnectivityEvent = {
      type: 'connectivity',
      target_name: 'orders',
      status: 'ok',
      latency_ms: 8,
      server_version: 'PostgreSQL 17.10 (Debian) on aarch64, compiled',
    }
    const props = renderRows({ connectivity: { orders: ok } })
    // The sweep on opening names failures only.
    expect(screen.queryByText('Connected to PostgreSQL 17.10.')).toBeNull()
    fireEvent.click(
      screen.getAllByRole('button', { name: 'Test connection' })[0]
    )
    expect(props.onTest).toHaveBeenCalledWith('orders')
    expect(screen.getByRole('status').textContent).toBe(
      'Connected to PostgreSQL 17.10.'
    )
  })

  it('says under the row what Test tunnel found', () => {
    const ssh = { host: 'bastion.test', port: 22, user: 'deploy' }
    const tunnelled = targets.map((target) => ({ ...target, ssh }))
    const props = renderRows({
      targets: tunnelled,
      connectivity: {},
      tunnelTests: {
        orders: { status: 'ok' },
        reports: {
          status: 'failed',
          message: "Couldn't reach the SSH jump host bastion.test:22.",
        },
      },
    })
    expect(screen.getByRole('status').textContent).toBe(
      'Tunnel works. The jump host and the database are reachable.'
    )
    expect(
      screen.getByText("Couldn't reach the SSH jump host bastion.test:22.")
    ).toBeTruthy()
    fireEvent.keyDown(
      screen.getByRole('button', { name: 'More actions for orders' }),
      { key: 'Enter' }
    )
    fireEvent.click(screen.getByRole('menuitem', { name: 'Test tunnel' }))
    expect(props.onTestTunnel).toHaveBeenCalledWith('orders')
  })

  it('says a tunnel test is running', () => {
    renderRows({
      targets: [{ ...targets[0], ssh: { host: 'b', port: 22, user: 'u' } }],
      connectivity: {},
      tunnelTests: { orders: { status: 'testing' } },
    })
    expect(screen.getByRole('status').textContent).toBe('Testing the tunnel...')
  })

  it('says which database waits for a sign-in, with the link', () => {
    signIn.held = new Set(['orders'])
    renderRows()
    const notes = screen.getAllByTestId('sign-in-note')
    expect(notes).toHaveLength(1)
    expect(notes[0].textContent).toBe('Sign in to run AI checks. Sign in')
    expect(
      screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')
    ).toBe('/account-login')
  })

  it('says so when there is no database yet', () => {
    renderRows({ targets: [], connectivity: {} })
    expect(screen.getByText('No database yet.')).toBeTruthy()
  })
})
