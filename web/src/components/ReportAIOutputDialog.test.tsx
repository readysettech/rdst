import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithClient } from '@/test-utils'
import { submitReport } from '../lib/api'
import { ReportAIOutputDialog } from './ReportAIOutputDialog'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return { ...actual, submitReport: vi.fn() }
})

describe('ReportAIOutputDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(submitReport).mockResolvedValue({ success: true })
  })

  afterEach(cleanup)

  it('sends the reviewed output as an anonymous AI-output report', async () => {
    const onClose = vi.fn()
    renderWithClient(
      <ReportAIOutputDialog
        output="SELECT secret FROM t"
        feature="ask"
        queryHash="abc"
        onClose={onClose}
      />
    )

    fireEvent.change(screen.getByLabelText('AI output to include'), {
      target: { value: 'SELECT [redacted] FROM t' },
    })
    fireEvent.change(screen.getByLabelText("What's wrong with it"), {
      target: { value: 'Leaks a column' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send report' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(submitReport).toHaveBeenCalledWith({
      reason: 'Leaks a column',
      sentiment: 'negative',
      email: undefined,
      query_hash: 'abc',
      feedback_kind: 'ai_output',
      feature: 'ask',
      ai_output: 'SELECT [redacted] FROM t',
    })
  })

  it('stays open and says why when the report is not accepted', async () => {
    vi.mocked(submitReport).mockResolvedValue({
      success: false,
      error: 'PostHog did not accept the feedback report. Please try again.',
    })
    const onClose = vi.fn()
    renderWithClient(
      <ReportAIOutputDialog output="text" feature="analyze" onClose={onClose} />
    )

    fireEvent.change(screen.getByLabelText("What's wrong with it"), {
      target: { value: 'Wrong' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send report' }))

    expect(await screen.findByText(/did not accept/)).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })
})
