import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  ANALYZE_CONSENT_MESSAGE,
  ANALYZE_CONSENT_TITLE,
  AnalyzeConsent,
} from './AnalyzeConsent'

afterEach(cleanup)

function props() {
  return {
    isOpen: true,
    skipFuturePrompts: false,
    onSkipFuturePrompts: vi.fn(),
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
  }
}

describe('AnalyzeConsent', () => {
  it('asks in a quiet dialog with a ghost cancel and one plain Analyze', () => {
    const p = props()
    renderWithClient(<AnalyzeConsent presentation="modal" {...p} />)
    const dialog = screen.getByRole('dialog', { name: ANALYZE_CONSENT_TITLE })
    expect(dialog.textContent).toContain(ANALYZE_CONSENT_MESSAGE)
    const analyze = screen.getByRole('button', { name: 'Analyze' })
    expect(analyze.querySelector('svg')).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', { name: "Don't ask again" }))
    expect(p.onSkipFuturePrompts).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(p.onCancel).toHaveBeenCalledTimes(1)
    fireEvent.click(analyze)
    expect(p.onConfirm).toHaveBeenCalledTimes(1)
  })

  it('asks inline as one alert line with the same actions', () => {
    const p = props()
    renderWithClient(<AnalyzeConsent presentation="inline" {...p} />)
    const inline = screen.getByTestId('analyze-consent-inline')
    expect(screen.getByRole('alert').textContent).toContain(
      ANALYZE_CONSENT_MESSAGE
    )
    expect(inline.textContent).toContain(ANALYZE_CONSENT_TITLE)
    const analyze = screen.getByRole('button', { name: 'Analyze' })
    expect(analyze.querySelector('svg')).toBeNull()
    fireEvent.click(analyze)
    expect(p.onConfirm).toHaveBeenCalledTimes(1)
  })

  it('renders nothing inline while closed', () => {
    renderWithClient(
      <AnalyzeConsent presentation="inline" {...props()} isOpen={false} />
    )
    expect(screen.queryByTestId('analyze-consent-inline')).toBeNull()
  })
})
