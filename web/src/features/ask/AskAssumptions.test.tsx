import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AskClarification } from '@/lib/ask'
import { AskAssumptions } from './AskAssumptions'

const assumed: AskClarification[] = [
  {
    id: 'ranking',
    term: 'debt',
    question: 'Rank by which debt measure?',
    answer: 'Debt-to-income ratio',
    options: ['Total loan amount', 'Debt-to-income ratio'],
    hints: ['ca.totloanamt', 'ei.debincratio'],
    assumed: true,
  },
  {
    id: 'top',
    term: 'top',
    question: 'How many customers?',
    answer: '10',
    options: [],
    examples: ['10', '25'],
    assumed: true,
  },
  {
    id: 'segment',
    term: 'small',
    question: 'What makes a segment small?',
    answer: '',
    assumed: true,
  },
]

describe('AskAssumptions', () => {
  afterEach(cleanup)

  it('lists what Ask assumed, including what it left to the data', () => {
    render(
      <AskAssumptions
        clarifications={assumed}
        disabled={false}
        onChange={vi.fn()}
      />
    )

    expect(screen.getByText('Ask assumed')).toBeTruthy()
    expect(screen.getByText('Debt-to-income ratio')).toBeTruthy()
    expect(screen.getByText('Ask worked it out from the data')).toBeTruthy()
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
  })

  it('changes one assumption and keeps the rest', () => {
    const onChange = vi.fn()
    render(
      <AskAssumptions
        clarifications={assumed}
        disabled={false}
        onChange={onChange}
      />
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Change: Rank by which debt measure?',
      })
    )
    // The current answer is shown picked, and running it unchanged does nothing.
    const current = screen.getByRole('radio', { name: /Debt-to-income ratio/ })
    expect(current.getAttribute('aria-checked')).toBe('true')
    const again = screen.getByRole('button', { name: 'Run again' })
    expect(again.hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByRole('radio', { name: /Total loan amount/ }))
    fireEvent.click(again)

    expect(onChange).toHaveBeenCalledWith([
      { ...assumed[0], answer: 'Total loan amount', assumed: false },
      assumed[1],
      assumed[2],
    ])
  })

  it('takes the user own words for a point Ask left to the data', () => {
    const onChange = vi.fn()
    render(
      <AskAssumptions
        clarifications={assumed}
        disabled={false}
        onChange={onChange}
      />
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Change: What makes a segment small?',
      })
    )
    fireEvent.change(
      screen.getByRole('textbox', { name: 'What makes a segment small?' }),
      { target: { value: '10 or fewer customers' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run again' }))

    expect(onChange.mock.calls[0][0][2]).toEqual({
      ...assumed[2],
      answer: '10 or fewer customers',
      assumed: false,
    })
  })

  it('starts the own-words answer from the current one, selected', () => {
    const onChange = vi.fn()
    render(
      <AskAssumptions
        clarifications={assumed}
        disabled={false}
        onChange={onChange}
      />
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Change: Rank by which debt measure?',
      })
    )
    fireEvent.click(screen.getByRole('radio', { name: /None of these/ }))
    const box = screen.getByRole('textbox', {
      name: 'Describe what you mean',
    }) as HTMLTextAreaElement
    expect(box.value).toBe('Debt-to-income ratio')
    expect(document.activeElement).toBe(box)
    expect([box.selectionStart, box.selectionEnd]).toEqual([
      0,
      box.value.length,
    ])
    fireEvent.change(box, {
      target: { value: 'Debt-to-income ratio, above 0.5 only' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run again' }))

    expect(onChange.mock.calls[0][0][0]).toEqual({
      ...assumed[0],
      answer: 'Debt-to-income ratio, above 0.5 only',
      assumed: false,
    })
  })

  it('runs again once with every point changed', () => {
    const onChange = vi.fn()
    render(
      <AskAssumptions
        clarifications={assumed}
        disabled={false}
        onChange={onChange}
      />
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Change: Rank by which debt measure?',
      })
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Change: How many customers?' })
    )
    fireEvent.click(screen.getByRole('radio', { name: /Total loan amount/ }))
    fireEvent.click(screen.getByRole('radio', { name: /^25/ }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Run again with 2 changes' })
    )

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith([
      { ...assumed[0], answer: 'Total loan amount', assumed: false },
      { ...assumed[1], answer: '25', assumed: false },
      assumed[2],
    ])
  })

  it('shows every point, the ones that only change what shows included', () => {
    const minor: AskClarification = {
      id: 'identifier',
      term: 'customer',
      question: 'Which ID should identify each customer?',
      answer: 'Client reference',
      assumed: true,
      minor: true,
    }
    render(
      <AskAssumptions
        clarifications={[assumed[0], minor]}
        disabled={false}
        onChange={vi.fn()}
      />
    )

    expect(screen.getByText('Debt-to-income ratio')).toBeTruthy()
    expect(screen.getByText('Client reference')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Show/ })).toBeNull()
  })

  it('says the user answered every point once they have', () => {
    render(
      <AskAssumptions
        clarifications={[{ ...assumed[0], assumed: false }]}
        disabled={false}
        onChange={vi.fn()}
      />
    )

    expect(screen.getByText('Your answers')).toBeTruthy()
    expect(screen.getByText('(your answer)')).toBeTruthy()
  })
})
