import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jsonResponse, renderWithClient } from '@/test-utils'
import { chatReport, submitReport } from '../lib/api'
import { FeedbackChat } from './FeedbackChat'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return { ...actual, chatReport: vi.fn(), submitReport: vi.fn() }
})

function type(text: string) {
  fireEvent.change(screen.getByLabelText('Message'), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('FeedbackChat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ email: 'me@example.com', verified: true })
        )
      )
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('sends the drafted feature request with the conversation attached', async () => {
    vi.mocked(chatReport).mockResolvedValue({
      reply: 'Got it.',
      suggestion: { kind: 'feature_request', summary: 'Add CSV export.' },
      ai: true,
    })
    vi.mocked(submitReport).mockResolvedValue({ success: true })
    renderWithClient(<FeedbackChat isOpen onClose={() => {}} />)

    type('I want CSV export')
    await screen.findByText('Got it.')
    expect(
      screen
        .getByRole('radio', { name: 'Feature request' })
        .getAttribute('aria-checked')
    ).toBe('true')
    await waitFor(() =>
      expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe(
        'me@example.com'
      )
    )

    fireEvent.click(screen.getByRole('button', { name: 'Send to the team' }))
    await waitFor(() => expect(submitReport).toHaveBeenCalled())
    expect(vi.mocked(submitReport).mock.calls[0][0]).toMatchObject({
      reason: 'Add CSV export.',
      kind: 'feature_request',
      email: 'me@example.com',
      transcript: 'User: I want CSV export\n\nRDST: Got it.',
    })
  })

  it('offers to send the message as typed when AI is unavailable', async () => {
    vi.mocked(chatReport).mockResolvedValue({
      reply: 'See the docs.',
      ai: false,
    })
    renderWithClient(<FeedbackChat isOpen onClose={() => {}} />)

    type('The Inbox is confusing')
    await screen.findByText('See the docs.')
    expect(
      (screen.getByLabelText('Note to the team') as HTMLTextAreaElement).value
    ).toBe('The Inbox is confusing')
  })

  it('keeps underscores in names like pg_stat_statements', async () => {
    vi.mocked(chatReport).mockResolvedValue({
      reply: 'Turn on pg_stat_statements first.',
      ai: true,
    })
    renderWithClient(<FeedbackChat isOpen onClose={() => {}} />)

    type('How do I turn on monitoring?')
    expect(
      await screen.findByText('Turn on pg_stat_statements first.')
    ).toBeTruthy()
  })

  it('shows raw HTML in a reply as text and drops script links', async () => {
    vi.mocked(chatReport).mockResolvedValue({
      reply:
        'Try <img src=x onerror="alert(1)"> or [this](javascript:alert(1)).',
      ai: true,
    })
    renderWithClient(<FeedbackChat isOpen onClose={() => {}} />)

    type('Help')
    expect(await screen.findByText(/<img src=x onerror/)).toBeTruthy()
    expect(screen.getByRole('log').querySelector('img')).toBeNull()
    expect(screen.getByText('this').closest('a')?.hasAttribute('href')).toBe(
      false
    )
  })
})
