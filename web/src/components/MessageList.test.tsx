import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { MessageList } from './MessageList'

// jsdom has no layout, so the list's scroll-to-bottom has nothing to call.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

const messages = [
  {
    id: 'm1',
    role: 'user' as const,
    parts: [{ type: 'text' as const, text: 'Why is this query slow?' }],
  },
  {
    id: 'm2',
    role: 'assistant' as const,
    parts: [{ type: 'text' as const, text: 'It scans the whole table.' }],
  },
]

afterEach(cleanup)

// Which side a bubble sits on and what colour it is may repeat the author;
// they must not be the only thing carrying it. [C-12]
describe('MessageList authorship', () => {
  it('names the author of every turn', () => {
    render(<MessageList messages={messages} />)

    expect(screen.getByText('You')).toBeTruthy()
    expect(screen.getByText('RDST')).toBeTruthy()
  })

  it('is a log, so a streamed reply is announced', () => {
    render(<MessageList messages={messages} />)

    const log = screen.getByRole('log')
    expect(log.getAttribute('aria-live')).toBe('polite')
  })
})
