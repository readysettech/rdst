import { cleanup, fireEvent, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient, renderWithClient } from '@/test-utils'
import {
  BUDGET_PROMPT,
  SigninBudgetCard,
  SigninSoftStrip,
  SOFT_PROMPT,
} from './SigninPrompts'

const track = vi.hoisted(() => vi.fn())
vi.mock('../lib/analytics', async (original) => ({
  ...(await original<typeof import('../lib/analytics')>()),
  trackEventOnce: track,
}))
vi.mock('./TrialRegistrationDialog', () => ({
  AccountLoginDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div role="dialog">Readyset account login</div> : null,
}))

function renderWith(node: ReactElement, account: Record<string, unknown>) {
  const client = createTestQueryClient()
  client.setQueryData(['account-status'], account)
  return renderWithClient(node, client)
}

const anonymous = (signin_prompt: string, percent_used = 30) => ({
  signed_in: false,
  is_anonymous: true,
  signin_prompt,
  percent_used,
  days_remaining: 20,
  trial_days: 30,
})

beforeEach(() => {
  track.mockClear()
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('SigninSoftStrip', () => {
  it('asks once a week has passed, and opens sign-in', () => {
    renderWith(<SigninSoftStrip />, anonymous('soft'))
    expect(screen.getByTestId('signin-soft').textContent).toContain(SOFT_PROMPT)
    expect(track).toHaveBeenCalledWith(
      'signin-prompt-soft',
      'signin_prompt_shown',
      {
        state: 'soft',
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByRole('dialog').textContent).toBe(
      'Readyset account login'
    )
  })

  it.each([
    'none',
    'budget',
    'required',
  ])('stays hidden while the prompt is %s', (prompt) => {
    renderWith(<SigninSoftStrip />, anonymous(prompt))
    expect(screen.queryByTestId('signin-soft')).toBeNull()
  })

  it('stays hidden for a signed-in account and an older backend', () => {
    renderWith(<SigninSoftStrip />, { signed_in: true, email: 'a@example.com' })
    expect(screen.queryByTestId('signin-soft')).toBeNull()
    cleanup()
    renderWith(<SigninSoftStrip />, { signed_in: false })
    expect(screen.queryByTestId('signin-soft')).toBeNull()
  })

  it('hides for three days when dismissed, then comes back', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
    renderWith(<SigninSoftStrip />, anonymous('soft'))
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.queryByTestId('signin-soft')).toBeNull()

    cleanup()
    vi.setSystemTime(new Date('2026-10-03T12:00:00Z'))
    renderWith(<SigninSoftStrip />, anonymous('soft'))
    expect(screen.queryByTestId('signin-soft')).toBeNull()

    cleanup()
    vi.setSystemTime(new Date('2026-10-04T12:00:01Z'))
    renderWith(<SigninSoftStrip />, anonymous('soft'))
    expect(screen.getByTestId('signin-soft')).toBeTruthy()
  })
})

describe('SigninBudgetCard', () => {
  it('says how much of the free AI is used and leads with sign-in', () => {
    renderWith(<SigninBudgetCard />, anonymous('budget', 84.4))
    const card = screen.getByTestId('signin-budget')
    expect(screen.getByRole('heading', { name: BUDGET_PROMPT })).toBeTruthy()
    expect(card.textContent).toContain(
      "You've used 84% of the free AI on this computer."
    )
    expect(track).toHaveBeenCalledWith(
      'signin-prompt-budget',
      'signin_prompt_shown',
      {
        state: 'budget',
      }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it.each([
    'none',
    'soft',
    'required',
  ])('stays out of the way while the prompt is %s', (prompt) => {
    renderWith(<SigninBudgetCard />, anonymous(prompt))
    expect(screen.queryByTestId('signin-budget')).toBeNull()
    expect(track).not.toHaveBeenCalled()
  })
})
