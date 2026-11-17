import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReportAIOutputButton } from './ReportAIOutputButton'

vi.mock('./ReportAIOutputDialog', () => ({
  ReportAIOutputDialog: ({
    feature,
    output,
  }: {
    feature: string
    output: string
  }) => (
    <div data-testid="report-dialog">
      {feature}:{output}
    </div>
  ),
}))

afterEach(cleanup)

describe('ReportAIOutputButton', () => {
  it('opens a reviewed report for the supplied surface output', () => {
    render(
      <ReportAIOutputButton feature="ask" output="Generated explanation" />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Report AI output' }))

    expect(screen.getByTestId('report-dialog').textContent).toBe(
      'ask:Generated explanation'
    )
  })

  it('does not render for empty output', () => {
    render(<ReportAIOutputButton feature="ask" output="  " />)

    expect(screen.queryByRole('button')).toBeNull()
  })
})
