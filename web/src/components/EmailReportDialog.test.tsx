import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jsonResponse, renderWithClient } from '@/test-utils'
import { emailAuditReport } from '../lib/api'
import { EmailReportDialog } from './EmailReportDialog'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    emailAuditReport: vi.fn(),
    emailFleetReport: vi.fn(),
  }
})

type Identity = { email: string | null; verified: boolean }

function stubFetch(identity: Identity, verifyPoll: { verified: boolean } = { verified: false }) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    void init
    const url = String(input)
    if (url === '/api/settings/email') {
      return Promise.resolve(
        jsonResponse({ ...identity, first_name: null, last_name: null })
      )
    }
    if (url === '/api/settings/email/verify-poll') {
      return Promise.resolve(jsonResponse(verifyPoll))
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('EmailReportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('confirms and sends when a verified address is on file', async () => {
    stubFetch({ email: 'ada@example.com', verified: true })
    vi.mocked(emailAuditReport).mockResolvedValue({
      status: 'sent',
      email: 'ada@example.com',
      verified: true,
    })

    renderWithClient(
      <EmailReportDialog isOpen onClose={() => {}} runId="audit_prod_1" />
    )

    await screen.findByText('ada@example.com')
    fireEvent.click(screen.getByRole('button', { name: /send report/i }))

    await waitFor(() => {
      expect(emailAuditReport).toHaveBeenCalledWith('audit_prod_1', undefined)
    })
    expect(await screen.findByText(/Report sent to ada@example.com/i)).toBeTruthy()
  })

  it('collects an address first when none is verified', async () => {
    const fetchMock = stubFetch({ email: null, verified: false })
    vi.mocked(emailAuditReport).mockResolvedValue({
      status: 'verification_sent',
      email: 'grace@example.com',
      verified: false,
    })

    renderWithClient(
      <EmailReportDialog isOpen onClose={() => {}} runId="audit_prod_1" />
    )

    const input = await screen.findByPlaceholderText('you@company.com')
    fireEvent.change(input, { target: { value: 'Grace@Example.com' } })
    fireEvent.click(screen.getByRole('button', { name: /send report/i }))

    await waitFor(() => {
      expect(emailAuditReport).toHaveBeenCalledWith(
        'audit_prod_1',
        'grace@example.com'
      )
    })
    // The address is registered through the settings endpoint before the send.
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === '/api/settings/email' && init?.method === 'POST'
      )
    ).toBe(true)
    expect(
      await screen.findByText(/Confirmation email sent to grace@example.com/i)
    ).toBeTruthy()
  })

  it('rejects a malformed address without calling the API', async () => {
    stubFetch({ email: null, verified: false })

    renderWithClient(
      <EmailReportDialog isOpen onClose={() => {}} runId="audit_prod_1" />
    )

    const input = await screen.findByPlaceholderText('you@company.com')
    fireEvent.change(input, { target: { value: 'not-an-email' } })
    fireEvent.click(screen.getByRole('button', { name: /send report/i }))

    expect(
      await screen.findByText(/Please enter a valid email address/i)
    ).toBeTruthy()
    expect(emailAuditReport).not.toHaveBeenCalled()
  })

  it('polls for verification and reports success once the link is clicked', async () => {
    vi.useFakeTimers()
    try {
      stubFetch({ email: 'ada@example.com', verified: false }, { verified: true })
      vi.mocked(emailAuditReport).mockResolvedValue({
        status: 'verification_sent',
        email: 'ada@example.com',
        verified: false,
      })

      renderWithClient(
        <EmailReportDialog isOpen onClose={() => {}} runId="audit_prod_1" />
      )

      await vi.waitFor(() =>
        expect(screen.getByPlaceholderText('you@company.com')).toBeTruthy()
      )
      fireEvent.click(screen.getByRole('button', { name: /send report/i }))
      await vi.waitFor(() =>
        expect(screen.getByText(/Confirmation email sent/i)).toBeTruthy()
      )

      await vi.advanceTimersByTimeAsync(3100)

      await vi.waitFor(() =>
        expect(screen.getByText(/Report sent to ada@example.com/i)).toBeTruthy()
      )
    } finally {
      vi.useRealTimers()
    }
  })
})
