import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalysisModePicker } from './AnalysisModePicker'

afterEach(() => cleanup())

describe('AnalysisModePicker', () => {
  it('shows the current mode and exposes both choices', () => {
    render(<AnalysisModePicker fast={false} onFastChange={vi.fn()} />)

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Analysis mode, Detailed analysis',
      })
    )

    expect(
      screen
        .getByRole('radio', { name: /Detailed analysis/ })
        .getAttribute('aria-checked')
    ).toBe('true')
    expect(
      screen
        .getByRole('radio', { name: /Quick analysis/ })
        .getAttribute('aria-checked')
    ).toBe('false')
    expect(screen.getByText('Recommended')).toBeTruthy()
  })

  it('selects quick analysis and closes the picker', () => {
    const onFastChange = vi.fn()
    render(<AnalysisModePicker fast={false} onFastChange={onFastChange} />)

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Analysis mode, Detailed analysis',
      })
    )
    fireEvent.click(screen.getByRole('radio', { name: /Quick analysis/ }))

    expect(onFastChange).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('radiogroup')).toBeNull()
  })
})
