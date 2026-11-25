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

    expect(
      screen.getByText('Ask uses its best guess for each question.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

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

  it('offers suggested answers to an open question without choosing one', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'top',
            question: 'How many of the top customers should be listed?',
            options: [],
            examples: ['10', '25', '100'],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    // Nothing is preselected.
    expect(
      screen
        .getAllByRole('radio')
        .some((radio) => radio.getAttribute('aria-checked') === 'true')
    ).toBe(false)
    fireEvent.click(screen.getByRole('radio', { name: /25/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(onRun).toHaveBeenCalledWith({ top: '25' })
  })

  it('takes a typed answer when none of the suggestions fits', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'top',
            question: 'How many of the top customers should be listed?',
            options: [],
            examples: ['10', '25', '100'],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    fireEvent.click(screen.getByRole('radio', { name: /None of these/ }))
    fireEvent.change(screen.getByPlaceholderText('Type your own answer'), {
      target: { value: '15' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(onRun).toHaveBeenCalledWith({ top: '15' })
  })

  it('shows an example value under an option and leaves out the column it reads', () => {
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'customer',
            question: 'Which ID should identify each customer?',
            options: ['Customer number', 'Registry number'],
            hints: ['core_record.clientref, e.g. CU241436', ''],
          },
        ])}
        disabled={false}
        onRun={vi.fn()}
      />
    )

    expect(screen.getByText('e.g. CU241436')).toBeTruthy()
    expect(screen.queryByText(/core_record/)).toBeNull()
  })

  it('sends a described answer when none of the options fits', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'customer',
            question: 'Which ID should identify each customer?',
            options: ['Customer number', 'Registry number'],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    fireEvent.click(screen.getByRole('radio', { name: /None of these/ }))
    const run = screen.getByRole('button', { name: 'Run' })
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Describe what you mean' }),
      {
        target: { value: ' The client reference on their income record ' },
      }
    )
    fireEvent.click(run)

    expect(onRun).toHaveBeenCalledWith({
      customer: 'The client reference on their income record',
    })
  })

  it('asks every question on one screen and leaves blank ones to Ask', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'customer',
            question: 'Which ID should identify each customer?',
            options: ['Customer number', 'Registry number'],
          },
          {
            id: 'top',
            question: 'How many customers?',
            options: [],
            examples: ['10', '25'],
          },
          {
            id: 'order',
            question: 'Which order?',
            options: ['Highest first', 'Lowest first'],
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    expect(screen.getByText('1/3')).toBeTruthy()
    expect(screen.getByText('3/3')).toBeTruthy()
    expect(
      screen.getByText('Ask uses its best guess for each question.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: /Registry number/ }))
    fireEvent.click(screen.getByRole('radio', { name: /^10/ }))
    expect(
      screen.getByText('Ask uses its best guess for the blank question.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(onRun).toHaveBeenCalledWith({
      customer: 'Registry number',
      top: '10',
    })
  })

  it('takes several options when the question allows them', () => {
    const onRun = vi.fn()
    render(
      <AskChoice
        clarification={clarification([
          {
            id: 'measures',
            question: 'Which debt measures should the summary show?',
            options: [
              'Debt-to-income ratio',
              'Total liabilities',
              'Loan amount',
            ],
            multiple: true,
          },
        ])}
        disabled={false}
        onRun={onRun}
      />
    )

    expect(screen.getByText(/Pick any that apply/)).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Loan amount' }))
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Debt-to-income ratio' })
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onRun).toHaveBeenLastCalledWith({
      measures: 'Loan amount; Debt-to-income ratio',
    })

    // "None of these" stands alone and takes a description instead.
    fireEvent.click(screen.getByRole('checkbox', { name: 'None of these' }))
    expect(
      screen
        .getByRole('checkbox', { name: 'Loan amount' })
        .getAttribute('aria-checked')
    ).toBe('false')
    // It starts from the picked readings, so a near miss takes a small edit.
    const box = screen.getByRole('textbox', { name: 'Describe what you mean' })
    expect((box as HTMLTextAreaElement).value).toBe(
      'Loan amount; Debt-to-income ratio'
    )
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Describe what you mean' }),
      { target: { value: 'Median debt-to-income' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onRun).toHaveBeenLastCalledWith({
      measures: 'Median debt-to-income',
    })
  })
})
