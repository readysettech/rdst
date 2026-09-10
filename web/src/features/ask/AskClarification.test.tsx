import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AskClarification } from './AskClarification'

const questions = [
  {
    id: 'revenue',
    question: 'Which revenue definition: gross or net?',
    options: ['Gross', 'Net'],
  },
]

afterEach(cleanup)

// The options claim `role="radio"`, so they owe the keyboard contract that
// role promises: one tab stop for the group, arrows to move, moving selects.
// [C-23]
describe('AskClarification options as a radio group', () => {
  it('gives the group a single tab stop', () => {
    render(<AskClarification questions={questions} onSubmit={vi.fn()} />)

    const radios = screen.getAllByRole('radio')
    expect(radios.map((radio) => radio.getAttribute('tabindex'))).toEqual([
      '0',
      '-1',
      '-1',
    ])
  })

  it('moves and selects with the arrow keys, and Home returns to the first', () => {
    render(<AskClarification questions={questions} onSubmit={vi.fn()} />)

    const group = screen.getByRole('radiogroup')
    const radios = screen.getAllByRole('radio')
    radios[0].focus()

    fireEvent.keyDown(group, { key: 'ArrowDown' })
    expect(screen.getAllByRole('radio')[1].getAttribute('aria-checked')).toBe(
      'true'
    )
    expect(document.activeElement).toBe(screen.getAllByRole('radio')[1])

    fireEvent.keyDown(group, { key: 'Home' })
    expect(screen.getAllByRole('radio')[0].getAttribute('aria-checked')).toBe(
      'true'
    )
  })

  it('wraps backwards from the first option to the last', () => {
    render(<AskClarification questions={questions} onSubmit={vi.fn()} />)

    const group = screen.getByRole('radiogroup')
    screen.getAllByRole('radio')[0].focus()
    fireEvent.keyDown(group, { key: 'ArrowUp' })

    const radios = screen.getAllByRole('radio')
    expect(radios[radios.length - 1].getAttribute('aria-checked')).toBe('true')
  })

  it('keeps the tab stop on the option the reader picked', () => {
    render(<AskClarification questions={questions} onSubmit={vi.fn()} />)

    fireEvent.click(screen.getByRole('radio', { name: /Net/ }))

    expect(
      screen
        .getAllByRole('radio')
        .map((radio) => radio.getAttribute('tabindex'))
    ).toEqual(['-1', '0', '-1'])
  })
})
