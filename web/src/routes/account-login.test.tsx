import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import { fetchAccountStatus, startAccountLogin } from '../lib/api'
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
  startAccountGithubOAuth: vi.fn(async () => undefined),
  startAccountGoogleOAuth: vi.fn(async () => undefined),
  sendAccountMagicLink: vi.fn(async () => undefined),
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
  it('is the one page with the rising purple, and says what signing in is for', () => {
    renderWithClient(<SignInPage />)

    expect(screen.getByRole('heading', { level: 1, name: 'RDST' })).toBeTruthy()
    expect(screen.getByText('Find slow queries. Prove the fix.')).toBeTruthy()
    const github = screen.getByRole('button', { name: 'Continue with GitHub' })
    const google = screen.getByRole('button', { name: 'Continue with Google' })
    expect(github.className).toContain('bg-surface-rising-solid')
    expect(google.className).not.toContain('bg-surface-rising-solid')
    expect(google.className).not.toContain('bg-surface-primary-solid')
    expect(
      screen.getByText(
        'Signing in turns on AI monitoring and the free AI included with a Readyset account for analyses, checks and Ask.'
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

  it('starts the provider sign-in from each button', async () => {
    renderWithClient(<SignInPage />)

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with GitHub' })
    )
    await waitFor(() =>
      expect(vi.mocked(startAccountGithubOAuth)).toHaveBeenCalledWith({
        login_id: 'login-1',
      })
    )
    expect(vi.mocked(startAccountLogin)).toHaveBeenCalledTimes(1)

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with Google' })
    )
    await waitFor(() =>
      expect(vi.mocked(startAccountGoogleOAuth)).toHaveBeenCalled()
    )
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
    await vi.advanceTimersByTimeAsync(2_000)
    await waitFor(() =>
      expect(router.navigate).toHaveBeenCalledWith({ href: '/inbox' })
    )
    vi.useRealTimers()
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
      screen.getByRole('button', { name: 'Continue with GitHub' })
    ).toBeTruthy()
  })
})
