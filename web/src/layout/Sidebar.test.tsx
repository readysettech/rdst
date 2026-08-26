import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { trackEvent } from '../lib/analytics'
import {
  __resetAuditSessionForTests,
  beginAuditSession,
  finishAuditSession,
} from '../lib/auditSession'
import { Sidebar } from './Sidebar'

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: '/' } }),
  useNavigate: () => vi.fn(),
  Link: ({
    to,
    children,
    className,
    onClick,
  }: {
    to: string
    children?: ReactNode
    className?: string
    onClick?: () => void
  }) => (
    <a href={to} className={className} onClick={onClick}>
      {children}
    </a>
  ),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: null }),
  useQueryClient: () => ({}),
}))

vi.mock('../components/TargetDropdown', () => ({
  TargetDropdown: () => <button type="button">target</button>,
}))

vi.mock('../components/ReportDialog', () => ({
  ReportDialog: () => null,
}))

vi.mock('../components/TrialBalanceBadge', () => ({
  TrialBalanceBadge: () => null,
}))

vi.mock('../components/TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? (
      <div role="dialog" aria-label="Free credits dialog">
        Free credits dialog
      </div>
    ) : null,
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'demo', setTarget: vi.fn() }),
}))

vi.mock('../lib/useSystemStatus', () => ({
  useSystemStatus: () => ({ data: undefined }),
}))

vi.mock('../lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

/** Wrapper with an external trigger so focus-return has a real target. */
function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <div>
      <button type="button" data-testid="hamburger">
        menu
      </button>
      <Sidebar mobileOpen={open} onMobileClose={onClose} />
    </div>
  )
}

describe('Sidebar mobile drawer a11y (T19 · USE-077/USE-090)', () => {
  afterEach(() => {
    cleanup()
    __resetAuditSessionForTests()
  })

  it('closes on Escape while open', () => {
    const onClose = vi.fn()
    render(<Sidebar mobileOpen onMobileClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not listen for Escape while closed', () => {
    const onClose = vi.fn()
    render(<Sidebar mobileOpen={false} onMobileClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('moves focus to the first nav item on open and returns it to the trigger on close', () => {
    const onClose = vi.fn()
    const { rerender } = render(<Harness open={false} onClose={onClose} />)
    screen.getByTestId('hamburger').focus()

    rerender(<Harness open onClose={onClose} />)
    const active = document.activeElement as HTMLElement
    expect(active.tagName).toBe('A')
    expect(active.getAttribute('href')).toBe('/')

    rerender(<Harness open={false} onClose={onClose} />)
    expect(document.activeElement).toBe(screen.getByTestId('hamburger'))
  })

  it('traps Tab within scrim + drawer while open (background not tabbable)', () => {
    render(<Sidebar mobileOpen onMobileClose={vi.fn()} />)
    // Last focusable inside the drawer set: the footer Discord link.
    const discord = screen.getByRole('link', { name: 'Join Discord' })
    discord.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    // Wraps to the first element of the trap set — the scrim.
    expect(
      (document.activeElement as HTMLElement).getAttribute('aria-label')
    ).toBe('Close navigation')

    // Shift+Tab from the scrim wraps back to the last element.
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(discord)
  })

  it('shows the Discord invite directly below Give feedback', () => {
    render(<Sidebar />)

    const feedback = screen.getByRole('button', { name: /Give feedback/ })
    const discord = screen.getByRole('link', { name: 'Join Discord' })
    expect(discord.getAttribute('href')).toBe('https://discord.gg/QkET8JhRwt')
    expect(discord.getAttribute('target')).toBe('_blank')
    expect(discord.getAttribute('rel')).toBe('noreferrer')
    expect(feedback.nextElementSibling).toBe(discord)
  })

  it('shows the desktop update action without backend version data', () => {
    const install = vi.fn()
    render(
      <Sidebar
        desktopUpdateState={{
          status: 'ready',
          version: '1.0.9',
          downloadLinks: [],
          progress: 100,
        }}
        onInstallUpdate={install}
      />
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Restart: Version 1.0.9 is ready',
      })
    )
    expect(install).toHaveBeenCalledTimes(1)
  })

  it('renders free credits as a quiet footer control, above Settings, and opens the trial dialog', () => {
    render(<Sidebar />)

    const credits = screen.getByRole('button', {
      name: /Get free AI credits/,
    })
    const settings = screen.getByRole('link', { name: /Settings/ })
    // F2: no gradient, colored border, or elevation shadow left to outshine
    // the nav — just the quiet footer-utility treatment.
    expect(credits.className).not.toContain('bg-gradient-to-r')
    expect(credits.className).not.toContain('shadow-elevation')
    expect(credits.className).toContain('border-0')
    expect(credits.className).toContain('h-8')
    expect(
      credits.compareDocumentPosition(settings) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()

    fireEvent.click(credits)
    expect(
      screen.getByRole('dialog', { name: 'Free credits dialog' })
    ).toBeTruthy()
  })

  it('sizes footer utilities below the daily nav (F5)', () => {
    render(<Sidebar />)
    const home = screen.getByRole('link', { name: /Home/ })
    const settings = screen.getByRole('link', { name: /Settings/ })
    const discord = screen.getByRole('link', { name: 'Join Discord' })
    expect(home.className).toContain('h-10')
    expect(home.className).toContain('font-medium')
    expect(settings.className).toContain('h-8')
    expect(settings.className).toContain('font-normal')
    expect(discord.className).toContain('h-8')
    expect(discord.className).toContain('font-normal')
    expect(discord.querySelector('svg')?.classList.contains('min-w-5')).toBe(
      true
    )
  })

  it('groups the footer into a status block and a utility block (F6)', () => {
    render(<Sidebar />)
    const status = screen.getByTestId('sidebar-footer-status')
    const utilities = screen.getByTestId('sidebar-footer-utilities')
    const credits = screen.getByRole('button', {
      name: /Get free AI credits/,
    })
    expect(utilities.contains(credits)).toBe(true)
    expect(
      status.compareDocumentPosition(utilities) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('gives nav links a focus-visible ring instead of the UA outline (F12)', () => {
    render(<Sidebar />)
    const home = screen.getByRole('link', { name: /Home/ })
    const docs = screen.getByRole('link', { name: 'Docs' })
    expect(home.className).toContain('focus-visible:ring-2')
    expect(docs.className).toContain('focus-visible:ring-2')
  })

  it('shows and clears the Health Check running indicator from audit session state', () => {
    const sessionId = beginAuditSession({
      kind: 'fleet',
      targetLabel: '2 targets',
      targetNames: ['alpha', 'beta'],
      durationSeconds: 60,
      startedAt: Date.now(),
      phase: 'capture',
      statusMessage: 'Capturing',
      cancel: vi.fn(),
    })

    render(<Sidebar />)
    expect(
      screen.getByRole('status', { name: 'Health check is running' })
    ).toBeTruthy()

    act(() => finishAuditSession(sessionId!))
    expect(
      screen.queryByRole('status', { name: 'Health check is running' })
    ).toBeNull()
  })
})

describe('Sidebar experimental surfaces (team decision: off the nav)', () => {
  afterEach(cleanup)

  it('does not list Code scan, Agents, Guards, or a group header', () => {
    render(<Sidebar />)
    expect(screen.queryByRole('link', { name: /Code scan/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Agents/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Guards/ })).toBeNull()
    expect(screen.queryByText('Experimental')).toBeNull()
    expect(screen.queryByText('Advanced')).toBeNull()
  })

  it('keeps Schema in the main nav', () => {
    render(<Sidebar />)
    expect(screen.getByRole('link', { name: /Schema/ })).toBeTruthy()
  })
})

describe('Sidebar nav_item_clicked analytics (E1)', () => {
  afterEach(cleanup)

  it('tracks the clicked item label', () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByRole('link', { name: /Ask/ }))
    expect(trackEvent).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Ask',
    })
  })

  it('tracks the Docs item', () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByRole('link', { name: 'Docs' }))
    expect(trackEvent).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Docs',
    })
  })

  it('tracks the Discord item', () => {
    render(<Sidebar />)
    const discord = screen.getByRole('link', { name: 'Join Discord' })
    discord.addEventListener('click', (event) => event.preventDefault(), {
      once: true,
    })
    fireEvent.click(discord)
    expect(trackEvent).toHaveBeenCalledWith('nav_item_clicked', {
      label: 'Discord',
    })
  })
})

describe('Sidebar value proposition (C1 / D-5)', () => {
  afterEach(cleanup)

  it('shows the one value-proposition line attached to the target selector', () => {
    render(<Sidebar />)
    expect(screen.getByText('Find slow queries. Prove the fix.')).toBeTruthy()
  })
})

describe('Sidebar Docs affordance (C4)', () => {
  afterEach(cleanup)

  it('links to the docs site, opened in a new tab', () => {
    render(<Sidebar />)
    const docs = screen.getByRole('link', { name: 'Docs' })
    expect(docs.getAttribute('href')).toBe('https://readyset.io/docs')
    expect(docs.getAttribute('target')).toBe('_blank')
    expect(docs.getAttribute('rel')).toBe('noreferrer')
  })

  it('places Docs near Settings and Give feedback in the footer', () => {
    render(<Sidebar />)
    const settings = screen.getByRole('link', { name: /Settings/ })
    const docs = screen.getByRole('link', { name: 'Docs' })
    const feedback = screen.getByRole('button', { name: /Give feedback/ })
    expect(
      settings.compareDocumentPosition(docs) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      docs.compareDocumentPosition(feedback) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })
})
