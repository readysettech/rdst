import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AskResultEvent } from '../../lib/ask'
import { AskComposer } from './AskComposer'
import { AskErrorState } from './AskErrorState'
import { AskResult } from './AskResult'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => (
    <a href="/">{children}</a>
  ),
  useNavigate: () => vi.fn(),
}))

afterEach(cleanup)

const composerProps = {
  question: '',
  disabled: false,
  examples: [],
  examplesLoading: false,
  examplesError: false,
  onQuestionChange: () => {},
  onSubmit: () => {},
  onExample: () => {},
  onRetryExamples: () => {},
}

function result(overrides: Partial<AskResultEvent>): AskResultEvent {
  return {
    type: 'result',
    sql: 'SELECT 1',
    columns: ['id'],
    rows: [],
    row_count: 0,
    execution_time_ms: 1.2,
    ...overrides,
  } as AskResultEvent
}

const resultProps = {
  question: 'How many orders shipped?',
  sqlGenerated: undefined,
  answeredFrom: 'orders_db',
  provenanceSource: 'semantic layer',
  executedSql: 'SELECT 1',
  limitAdded: false,
  onViewInQueries: () => {},
  onAnalyze: () => {},
  onRefine: () => {},
  onNewQuestion: () => {},
}

describe('AskComposer target provenance', () => {
  it('never names a database the app was not told to use', () => {
    render(<AskComposer {...composerProps} target={null} />)

    expect(screen.queryByText(/Read-only on/)).toBeNull()
    expect(
      screen.getByText('No database selected — choose one before asking')
    ).toBeTruthy()
  })

  it('names the resolved target', () => {
    render(<AskComposer {...composerProps} target="orders_db" />)

    expect(screen.getByText('orders_db')).toBeTruthy()
  })
})

describe('AskResult outcome header', () => {
  it('does not stamp a zero-row result as a verified answer', () => {
    render(
      <AskResult {...resultProps} result={result({ rows: [], row_count: 0 })} />
    )

    expect(screen.queryByText('Verified answer')).toBeNull()
    expect(screen.getByText('Query ran, no rows')).toBeTruthy()
  })

  it('keeps the verified header when rows came back', () => {
    render(
      <AskResult
        {...resultProps}
        result={result({ rows: [[1]], row_count: 1 })}
      />
    )

    expect(screen.getByText('Verified answer')).toBeTruthy()
  })

  it('offers the zero-row next step from the shared empty state', () => {
    const onRefine = vi.fn()
    render(
      <AskResult
        {...resultProps}
        onRefine={onRefine}
        result={result({ rows: [], row_count: 0 })}
      />
    )

    // EmptyState renders its title as a heading and centres its one action,
    // which the hand-rolled block did not (C-31/C-33).
    expect(
      screen.getByRole('heading', { name: 'No rows matched' })
    ).toBeTruthy()
    screen.getByRole('button', { name: /Edit question/ }).click()
    expect(onRefine).toHaveBeenCalledTimes(1)
  })

  it('right-aligns numeric columns and leaves text alone', () => {
    render(
      <AskResult
        {...resultProps}
        result={result({
          columns: ['customer', 'revenue'],
          rows: [
            ['Ada', 4200],
            ['Grace', 3100],
          ],
          row_count: 2,
        })}
      />
    )

    const [text, numeric] = screen.getAllByRole('columnheader')
    expect(text.className).not.toContain('text-right')
    expect(numeric.className).toContain('text-right')
  })
})

describe('AskErrorState', () => {
  const errorProps = {
    error: {
      type: 'error' as const,
      message: 'The SQL generator is temporarily unavailable',
      phase: 'generate',
    },
    target: 'orders_db',
    onStartTrial: () => {},
  }

  it('shows the question it offers to re-run', () => {
    render(
      <AskErrorState
        {...errorProps}
        question="How many orders shipped?"
        onRetry={() => {}}
        onRefine={() => {}}
      />
    )

    // C-27: the composer unmounts on failure, so the failed question has to
    // be visible here or the retry has no subject.
    expect(screen.getByText('How many orders shipped?')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy()
  })

  it('routes the way back to the composer that keeps the question', () => {
    const onRefine = vi.fn()
    render(
      <AskErrorState
        {...errorProps}
        question="How many orders shipped?"
        onRetry={() => {}}
        onRefine={onRefine}
      />
    )

    screen.getByRole('button', { name: /Edit question/ }).click()
    expect(onRefine).toHaveBeenCalledTimes(1)
  })
})
