import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jsonResponse, renderWithClient } from '@/test-utils'
import { submitReport } from '../lib/api'
import { ReportDialog } from './ReportDialog'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchQueryRegistry: vi.fn().mockResolvedValue({ queries: [] }),
    submitReport: vi.fn(),
  }
})

function stubIdentity(email: string | null, verified: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        jsonResponse({ email, verified, first_name: null, last_name: null })
      )
    )
  )
}

describe('ReportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    stubIdentity(null, false)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('requires an email before feedback can be sent', () => {
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'The analysis was useful.' },
    })

    expect(
      (
        screen.getByRole('button', {
          name: 'Send Feedback',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(submitReport).not.toHaveBeenCalled()
  })

  it('rejects a malformed email without submitting feedback', () => {
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'The analysis was useful.' },
    })
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'not-an-email' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send Feedback' }))

    expect(submitReport).not.toHaveBeenCalled()
  })

  it('normalizes the email and includes it in the feedback event', async () => {
    vi.mocked(submitReport).mockResolvedValue({ success: true, error: null })
    const onClose = vi.fn()
    renderWithClient(<ReportDialog isOpen onClose={onClose} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: '  The analysis was useful.  ' },
    })
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: ' Feedback@Example.COM ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send Feedback' }))

    await waitFor(() => {
      expect(submitReport).toHaveBeenCalledWith(
        expect.objectContaining({
          reason: 'The analysis was useful.',
          email: 'feedback@example.com',
        })
      )
    })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('uses a verified email without asking for it', async () => {
    stubIdentity('ada@example.com', true)
    vi.mocked(submitReport).mockResolvedValue({ success: true, error: null })
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    expect(await screen.findByText('Sending as ada@example.com')).toBeTruthy()
    expect(screen.queryByPlaceholderText('you@example.com')).toBeNull()

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'The analysis was useful.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send Feedback' }))

    await waitFor(() => {
      expect(submitReport).toHaveBeenCalledWith(
        expect.objectContaining({ email: 'ada@example.com' })
      )
    })
  })

  it('validates an override of the verified email', async () => {
    stubIdentity('ada@example.com', true)
    vi.mocked(submitReport).mockResolvedValue({ success: true, error: null })
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Use a different email' })
    )
    const emailInput = screen.getByPlaceholderText('you@example.com')

    fireEvent.change(emailInput, { target: { value: 'not-an-email' } })
    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'The analysis was useful.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send Feedback' }))
    expect(submitReport).not.toHaveBeenCalled()

    fireEvent.change(emailInput, { target: { value: ' Grace@Example.COM ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send Feedback' }))

    await waitFor(() => {
      expect(submitReport).toHaveBeenCalledWith(
        expect.objectContaining({ email: 'grace@example.com' })
      )
    })
  })
})
