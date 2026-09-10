import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SidebarAccountMenu } from './SidebarAccountMenu'

const state = vi.hoisted(() => ({
  accountStatus: null as { signed_in: boolean; email?: string | null } | null,
  logout: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: state.accountStatus }),
  useQueryClient: () => ({}),
  useMutation: ({ mutationFn }: { mutationFn: () => Promise<unknown> }) => ({
    mutate: () => void mutationFn(),
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
  it('renders nothing when no Readyset account is signed in', () => {
    state.accountStatus = { signed_in: false, email: 'stale@example.com' }
    const { container } = render(<SidebarAccountMenu />)

    expect(container.firstChild).toBeNull()
  })

  it('makes the signed-in email a control, not an inert line', () => {
    state.accountStatus = { signed_in: true, email: 'mike.v@readyset.io' }
    render(<SidebarAccountMenu />)

    const trigger = screen.getByRole('button', {
      name: 'Account: mike.v@readyset.io',
    })
    expect(trigger.textContent).toContain('mike.v@readyset.io')
  })

  it('offers Sign out and the AI settings from the account menu', () => {
    state.accountStatus = { signed_in: true, email: 'mike.v@readyset.io' }
    render(<SidebarAccountMenu />)
    openMenu()

    expect(screen.getByRole('menuitem', { name: /Sign out/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('menuitem', { name: /Manage AI access/ }))
    expect(state.navigate).toHaveBeenCalledWith({
      to: '/configure',
      search: { panel: 'ai' },
    })
  })

  it('confirms before signing out, and cancelling keeps the session', () => {
    state.accountStatus = { signed_in: true, email: 'mike.v@readyset.io' }
    render(<SidebarAccountMenu />)
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }))

    expect(screen.getByText('Sign out of Readyset?')).toBeTruthy()
    expect(state.logout).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(state.logout).not.toHaveBeenCalled()
  })

  it('signs out only on the confirm', () => {
    state.accountStatus = { signed_in: true, email: 'mike.v@readyset.io' }
    render(<SidebarAccountMenu />)
    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(state.logout).toHaveBeenCalledTimes(1)
  })
})
