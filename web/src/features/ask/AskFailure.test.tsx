import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AskFailure } from './AskFailure'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: unknown }) => (
    <a href="/">{children as never}</a>
  ),
}))

afterEach(cleanup)

const renderFailure = (message: string) =>
  render(
    <AskFailure
      error={{ type: 'error', phase: 'execute', message } as never}
      target="demo"
      sql=""
      limitAdded={false}
      onEdit={vi.fn()}
      onRetry={vi.fn()}
    />
  )

describe('AskFailure', () => {
  it('names the refusal on one line with the hint under it', () => {
    renderFailure('permission denied for table votes')
    const line = screen.getByRole('alert')
    expect(line.textContent).toContain(
      'demo refused the query: permission denied for table votes.'
    )
    expect(line.textContent).toContain(
      'Ask RDST to try without that table, or grant SELECT.'
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('carries no hint for other errors', () => {
    renderFailure('relation "votes" does not exist')
    const line = screen.getByRole('alert')
    expect(line.textContent).toContain(
      'demo refused the query: relation "votes" does not exist.'
    )
    expect(line.textContent).not.toContain('grant SELECT')
  })

  it('shows a schema too large for the model without Sign in or Try again', () => {
    render(
      <AskFailure
        error={
          {
            type: 'error',
            phase: 'generate',
            code: 'ANTHROPIC_CONTEXT_WINDOW_EXCEEDED',
            category: 'model-limit',
            message:
              "The complete database schema could not fit within Anthropic's model context window.",
          } as never
        }
        target="demo"
        sql=""
        limitAdded={false}
        onEdit={vi.fn()}
        onRetry={vi.fn()}
      />
    )
    expect(screen.getByTestId('ask-failure').textContent).toContain(
      'The complete database schema could not fit'
    )
    expect(screen.queryByRole('link', { name: 'Sign in' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Edit question' })).toBeTruthy()
  })
})
