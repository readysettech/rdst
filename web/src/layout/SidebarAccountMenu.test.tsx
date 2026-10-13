import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SidebarAccountMenu } from './SidebarAccountMenu'

const state = vi.hoisted(() => ({
  accountStatus: null as { signed_in: boolean; email?: string | null } | null,
  logout: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
  Link: ({
    to,
    children,
    ...rest
  }: {
    to: string
    children?: ReactNode
  } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: state.accountStatus }),
  useQueryClient: () => ({}),
  useMutation: ({
    mutationFn,
    onSuccess,
  }: {
    mutationFn: () => Promise<unknown>
    onSuccess: () => Promise<void>
  }) => ({
    mutate: () => void mutationFn().then(onSuccess),
    isPending: false,
  }),
}))

vi.mock('../lib/api', () => ({
  fetchAccountStatus: vi.fn(),
  logoutAccount: () => {
    state.logout()
    return Promise.resolve()
  },
}))

vi.mock('../lib/trialQueries', () => ({
  invalidateTrialRelatedQueries: vi.fn(async () => {}),
}))

afterEach(() => {
  cleanup()
  state.accountStatus = null
  state.logout.mockClear()
  state.navigate.mockClear()
})

function openMenu() {
  // Radix opens the menu from pointerdown/keydown, not from a synthetic click.
  fireEvent.keyDown(screen.getByRole('button', { name: /Account:/ }), {
    key: 'Enter',
  })
}

describe('SidebarAccountMenu', () => {
  it('is the way to the sign-in page when no account is signed in', () => {
    state.accountStatus = { signed_in: false, email: 'stale@example.com' }
    render(<SidebarAccountMenu />)

    expect(screen.queryByText('stale@example.com')).toBeNull()
    expect(
      screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')
    ).toBe('/account-login')
  })

  it('makes the signed-in email a control, not an inert line', () => {
    state.accountStatus = { signed_in: true, email: 'khalid@readyset.io' }
    render(<SidebarAccountMenu />)

    const trigger = screen.getByRole('button', {
      name: 'Account: khalid@readyset.io',
    })
    expect(trigger.textContent).toBe('khalid@readyset.io')
  })

  it('repeats the email, then Settings, Help and Sign out alone', () => {
    state.accountStatus = { signed_in: true, email: 'khalid@readyset.io' }
    render(<SidebarAccountMenu />)
    openMenu()

    const items = screen
      .getAllByRole('menuitem')
      .map((item) => item.textContent)
    expect(items).toEqual(['Settings', 'Help', 'Sign out'])
    expect(screen.getAllByText('khalid@readyset.io')).toHaveLength(2)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Help' }))
    expect(state.navigate).toHaveBeenCalledWith({ to: '/help' })
  })

  it('signs out on the click and returns to the sign-in page', async () => {
    state.accountStatus = { signed_in: true, email: 'khalid@readyset.io' }
    render(<SidebarAccountMenu />)
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }))

    expect(state.logout).toHaveBeenCalledTimes(1)
    await waitFor(() =>
      expect(state.navigate).toHaveBeenCalledWith({ to: '/account-login' })
    )
  })
})

it('shows the account menu for a session that carries no email', () => {
  state.accountStatus = { signed_in: true, email: null }
  render(<SidebarAccountMenu />)
  expect(screen.queryByTestId('sidebar-sign-in')).toBeNull()
  expect(
    screen.getByRole('button', { name: 'Account: Signed in' })
  ).toBeTruthy()
})
