import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import {
  fetchAccountBrowserCallback,
  fetchAccountStatus,
  startAccountLogin,
} from '../lib/api'
import { SignInPage } from './-sign-in-page'

const router = vi.hoisted(() => ({
  navigate: vi.fn(),
  back: vi.fn(),
  canGoBack: true,
}))

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => router.navigate,
  useRouter: () => ({ history: { back: router.back } }),
  useCanGoBack: () => router.canGoBack,
}))

vi.mock('../lib/api', () => ({
  startAccountLogin: vi.fn(async () => ({ login_id: 'login-1' })),
  fetchAccountBrowserCallback: vi.fn(),
  fetchAccountStatus: vi.fn(async () => ({ signed_in: false })),
}))

vi.mock('../lib/accountAuth', () => ({
  startAccountGoogleOAuth: vi.fn(async () => undefined),
  sendAccountMagicLink: vi.fn(async () => 'sent' as const),
  completeAccountRedirect: vi.fn(),
}))

vi.mock('../lib/trialQueries', () => ({
  invalidateTrialRelatedQueries: vi.fn(async () => {}),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  router.canGoBack = true
})

describe('SignInPage', () => {
  it('offers Google and an emailed link, and says what signing in is for', () => {
    renderWithClient(<SignInPage />)

    expect(screen.getByRole('heading', { level: 1, name: 'RDST' })).toBeTruthy()
    expect(screen.getByText('Find slow queries. Prove the fix.')).toBeTruthy()
    const google = screen.getByRole('button', { name: 'Continue with Google' })
    expect(google.className).not.toContain('bg-surface-primary-solid')
    expect(
      screen.queryByRole('button', { name: 'Continue with GitHub' })
    ).toBeNull()
    expect(
      screen.getByText(
        'A Readyset account is free and comes with AI for monitoring, analyses, checks and Ask.'
      )
    ).toBeTruthy()
    expect(document.getElementById('app-sidebar')).toBeNull()
  })

  it('goes back to the page it came from, or Home when opened directly', () => {
    renderWithClient(<SignInPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(router.back).toHaveBeenCalledOnce()
    cleanup()
    router.canGoBack = false
    renderWithClient(<SignInPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(router.navigate).toHaveBeenCalledWith({ to: '/' })
  })

  it('goes back on Escape, as Back does', () => {
    renderWithClient(<SignInPage />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(router.back).toHaveBeenCalledOnce()
  })

  it('starts the Google sign-in from its button', async () => {
    renderWithClient(<SignInPage />)

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with Google' })
    )
    await waitFor(() =>
      expect(vi.mocked(startAccountGoogleOAuth)).toHaveBeenCalledWith({
        login_id: 'login-1',
      })
    )
    expect(vi.mocked(startAccountLogin)).toHaveBeenCalledTimes(1)
  })

  it('sends an emailed sign-in link and finishes when it is opened', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    localStorage.setItem('rdst_login_return_to', '/inbox')
    renderWithClient(<SignInPage />)
    const field = screen.getByLabelText('Email address')
    const send = screen.getByRole('button', { name: 'Send sign-in link' })
    expect((send as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(field, { target: { value: 'not-an-email' } })
    fireEvent.blur(field)
    expect(screen.getByText('Enter a valid email address.')).toBeTruthy()

    fireEvent.change(field, { target: { value: 'qa@readyset.io' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))
    expect(await screen.findByText('Check your email')).toBeTruthy()
    expect(vi.mocked(sendAccountMagicLink)).toHaveBeenCalledWith(
      { login_id: 'login-1' },
      'qa@readyset.io'
    )
    expect(screen.getByText(/sent to qa@readyset\.io/)).toBeTruthy()

    vi.mocked(fetchAccountStatus).mockResolvedValue({
      signed_in: true,
    } as Awaited<ReturnType<typeof fetchAccountStatus>>)
    await vi.advanceTimersByTimeAsync(3_000)
    await waitFor(() =>
      expect(router.navigate).toHaveBeenCalledWith({ href: '/inbox' })
    )
    vi.useRealTimers()
  })

  it('carries on when a link from before a reload signs in', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    localStorage.setItem('rdst_login_return_to', '/configure')
    vi.mocked(fetchAccountStatus).mockResolvedValue({
      signed_in: false,
    } as Awaited<ReturnType<typeof fetchAccountStatus>>)
    renderWithClient(<SignInPage />)
    await screen.findByLabelText('Email address')
    await vi.advanceTimersByTimeAsync(0)
    expect(router.navigate).not.toHaveBeenCalled()

    vi.mocked(fetchAccountStatus).mockResolvedValue({
      signed_in: true,
    } as Awaited<ReturnType<typeof fetchAccountStatus>>)
    await vi.advanceTimersByTimeAsync(3_000)
    await waitFor(() =>
      expect(router.navigate).toHaveBeenCalledWith({ href: '/configure' })
    )
    expect(router.navigate).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('finishes a desktop sign-in RDST completed from the link', async () => {
    window.rdstDesktop = { isDesktop: true, platform: 'linux' }
    localStorage.setItem('rdst_login_return_to', '/configure')
    const callback = { code: null, error: null, error_description: null }
    vi.mocked(fetchAccountBrowserCallback)
      .mockResolvedValueOnce({ state: 'completing', ...callback })
      .mockResolvedValue({ state: 'success', ...callback })
    renderWithClient(<SignInPage />)

    fireEvent.change(screen.getByLabelText('Email address'), {
      target: { value: 'qa@readyset.io' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))

    await waitFor(() =>
      expect(router.navigate).toHaveBeenCalledWith({ href: '/configure' })
    )
    expect(vi.mocked(fetchAccountBrowserCallback)).toHaveBeenCalledTimes(2)
    expect(vi.mocked(completeAccountRedirect)).not.toHaveBeenCalled()
    delete window.rdstDesktop
  })

  it('finishes a provider redirect inside the sign-in column', async () => {
    vi.mocked(completeAccountRedirect).mockReturnValue(new Promise(() => {}))
    renderWithClient(<SignInPage callbackLoginId="login-9" />)

    expect(screen.getByText('Finishing sign-in...')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: 'RDST' })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() =>
      expect(vi.mocked(completeAccountRedirect)).toHaveBeenCalledWith(
        'login-9',
        undefined
      )
    )
  })

  it('offers the providers again when the redirect cannot finish', async () => {
    vi.mocked(completeAccountRedirect).mockRejectedValue(new Error('expired'))
    renderWithClient(<SignInPage callbackLoginId="login-9" />)

    expect(
      await screen.findByText(
        'This sign-in link has expired or was already used.'
      )
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Continue with Google' })
    ).toBeTruthy()
  })

  it('turns a spent link into a sentence and a way to send a new one', async () => {
    const raw = 'This sign-in session is unknown, expired, or already completed'
    vi.mocked(completeAccountRedirect).mockRejectedValue(new Error(raw))
    renderWithClient(<SignInPage callbackLoginId="login-9" />)

    expect(
      await screen.findByText(
        'This sign-in link has expired or was already used.'
      )
    ).toBeTruthy()
    expect(screen.queryByText(raw)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    expect(router.navigate).toHaveBeenCalledWith({
      to: '/account-login',
      search: {},
      replace: true,
    })
    const email = await screen.findByLabelText('Email address')
    await waitFor(() => expect(document.activeElement).toBe(email))
    expect(screen.queryByText('Finishing sign-in...')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Send a new link' })).toBeNull()
  })
})
