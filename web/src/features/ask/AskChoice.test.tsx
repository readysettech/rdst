import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AskClarificationNeededEvent } from '@/lib/ask'
import { AskChoice } from './AskChoice'

function clarification(
  questions: AskClarificationNeededEvent['questions']
): AskClarificationNeededEvent {
  return {
    type: 'clarification_needed',
    session_id: 'session-1',
    interpretations: [],
    questions,
  } as AskClarificationNeededEvent
}

describe('AskChoice', () => {
  afterEach(cleanup)

  it('skips a blank open question instead of requiring an answer', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'missing-1',
            question: "I couldn't find QI in this database.",
            options: [],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(onRun).toHaveBeenCalledWith({})
  })

  it('sends a typed answer for an open question', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'cutoff',
            question: 'What glucose level counts as high?',
            options: [],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: ' Above 180 ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(onRun).toHaveBeenCalledWith({ cutoff: 'Above 180' })
  })
})
