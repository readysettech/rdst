import { toast } from '@rs/ui-new/use-toast'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, renderWithClient } from '@/test-utils'
import { fetchQueryRegistry, submitReport } from '../lib/api'
import { ReportDialog } from './ReportDialog'

vi.mock('@rs/ui-new/use-toast', () => ({ toast: vi.fn() }))

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

  it('asks in the shared dialog: sentiment segments, one plain Send', () => {
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Give feedback' })).toBeTruthy()
    expect(screen.getByText('Goes straight to the team.')).toBeTruthy()
    const segments = screen.getAllByRole('radio')
    expect(segments.map((segment) => segment.textContent)).toEqual([
      'Great',
      'Okay',
      'Poor',
    ])
    fireEvent.click(screen.getByRole('radio', { name: 'Great' }))
    expect(screen.getByText('What did RDST do well?')).toBeTruthy()
    const send = screen.getByRole('button', { name: 'Send' })
    expect(send.className).not.toContain('rising')
    expect(send.querySelector('svg')).toBeNull()
  })

  it('requires an email before feedback can be sent', () => {
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'The analysis was useful.' },
    })

    expect(
      (
        screen.getByRole('button', {
          name: 'Send',
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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(submitReport).not.toHaveBeenCalled()

    fireEvent.change(emailInput, { target: { value: ' Grace@Example.COM ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(submitReport).toHaveBeenCalledWith(
        expect.objectContaining({ email: 'grace@example.com' })
      )
    })
  })

  it('keeps the text and says so when the feedback never left the machine', async () => {
    vi.mocked(submitReport).mockResolvedValue({
      success: false,
      error:
        "Couldn't send your feedback. Check your connection and try again.",
    })
    const onClose = vi.fn()
    renderWithClient(<ReportDialog isOpen onClose={onClose} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'Offline feedback' },
    })
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'a@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith({
        title:
          "Couldn't send your feedback. Check your connection and try again.",
        variant: 'negative',
      })
    )
    expect(onClose).not.toHaveBeenCalled()
    expect(
      (
        screen.getByPlaceholderText(
          'Share your thoughts...'
        ) as HTMLTextAreaElement
      ).value
    ).toBe('Offline feedback')
  })

  it('says the same when the local server could not be reached', async () => {
    vi.mocked(submitReport).mockRejectedValue(new TypeError('Failed to fetch'))
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'Hello' },
    })
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'a@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title:
            "Couldn't send your feedback. Check your connection and try again.",
        })
      )
    )
  })

  it('picks the related query with the keyboard', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    Element.prototype.hasPointerCapture = vi.fn(() => false)
    Element.prototype.releasePointerCapture = vi.fn()
    vi.mocked(fetchQueryRegistry).mockResolvedValueOnce({
      queries: [
        { hash: 'abcdef1234', tag: 'Orders', sql: 'SELECT * FROM orders' },
      ],
    } as never)
    vi.mocked(submitReport).mockResolvedValue({ success: true, error: null })
    renderWithClient(<ReportDialog isOpen onClose={() => {}} />)

    const picker = screen.getByRole('combobox', {
      name: 'Related query (optional)',
    })
    expect(picker.textContent).toContain('None - General feedback')
    picker.focus()
    fireEvent.keyDown(picker, { key: 'ArrowDown' })
    const option = await screen.findByRole('option', {
      name: 'Orders: SELECT * FROM orders',
    })
    fireEvent.keyDown(option, { key: 'Enter' })
    await waitFor(() => expect(picker.textContent).toContain('Orders'))

    fireEvent.change(screen.getByPlaceholderText('Share your thoughts...'), {
      target: { value: 'About this query' },
    })
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'a@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(submitReport).toHaveBeenCalledWith(
        expect.objectContaining({ query_hash: 'abcdef1234' })
      )
    )
  })
})
