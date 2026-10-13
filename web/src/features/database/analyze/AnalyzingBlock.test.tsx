import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AnalyzingBlock } from './AnalyzingBlock'
import type { AnalyzingStep } from './analyzingSteps'

afterEach(cleanup)

const steps: AnalyzingStep[] = [
  { label: 'Sample parameters', state: 'done' },
  { label: 'Read the plan', state: 'now' },
  { label: 'Ask the AI', state: 'todo' },
]

it('offers Cancel as a ghost when the block can stop the analysis', () => {
  const onCancel = vi.fn()
  render(<AnalyzingBlock steps={steps} aboutSeconds={18} onCancel={onCancel} />)
  expect(screen.getByText('About 18 s')).toBeTruthy()
  const cancel = screen.getByRole('button', { name: 'Cancel' })
  expect(cancel.className.includes('bg-surface-primary-solid')).toBe(false)
  fireEvent.click(cancel)
  expect(onCancel).toHaveBeenCalledTimes(1)
})

it('shows no button without a way to cancel', () => {
  render(<AnalyzingBlock steps={steps} aboutSeconds={null} />)
  expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  expect(
    screen.getByTestId('analyzing-block').querySelectorAll('li')
  ).toHaveLength(3)
})
