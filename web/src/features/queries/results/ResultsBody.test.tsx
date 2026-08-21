import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResultsBody } from './ResultsBody'
import type { ResultsController } from './useResultsController'

// The parameter form pulls in the CodeMirror SQL stack; here only the shell it
// is asked for matters.
vi.mock('../../../components/top', () => ({
  hasParameters: () => true,
  ParameterDialog: ({
    isOpen,
    presentation,
  }: {
    isOpen: boolean
    presentation?: string
  }) =>
    isOpen ? (
      <div
        data-testid="parameter-form"
        data-presentation={presentation ?? 'modal'}
      />
    ) : null,
}))
vi.mock('./AnalysisResults', () => ({
  AnalysisResults: () => <div data-testid="analysis-results" />,
}))
vi.mock('../../../components/QueryCard', () => ({
  QueryCard: () => <div data-testid="query-card" />,
}))

function controller(
  overrides: {
    parameters?: Partial<ResultsController['parameters']>
    consent?: Partial<ResultsController['consent']>
  } = {}
): ResultsController {
  return {
    query: { sql: 'SELECT 1', target: 'prod', fast: false },
    origin: 'query-library',
    backLabel: 'Back to queries',
    analysis: {
      state: 'idle',
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      errorEnvelope: undefined,
    },
    stored: {
      isActive: false,
      isLoading: false,
      hasBody: false,
      error: null,
      record: null,
      history: [],
      ageBucket: null,
    },
    passwordLock: { isLocked: false, isResolved: true },
    connectivity: { failure: null, isChecking: false },
    cache: { isPending: false },
    parameters: {
      hasParameters: false,
      isOpen: false,
      initialValues: undefined,
      ...overrides.parameters,
    },
    consent: { isOpen: false, skipFuturePrompts: false, ...overrides.consent },
    chat: { isOpen: false, hasExisting: false, results: {} },
    actions: {
      setSkipAnalyzeConsent: vi.fn(),
      confirmAnalysis: vi.fn(),
      cancelAnalysis: vi.fn(),
      cancelParameters: vi.fn(),
      submitParameters: vi.fn(),
      openParameters: vi.fn(),
    },
  } as unknown as ResultsController
}

afterEach(cleanup)

describe('pre-run steps in the analyze drawer', () => {
  it('asks for consent in the body, with no dialog over the drawer', () => {
    render(
      <ResultsBody
        controller={controller({ consent: { isOpen: true } })}
        prompts="inline"
      />
    )

    expect(screen.getByTestId('analyze-consent-inline')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run analyze' })).toBeTruthy()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    // The results shell waits for the answer rather than showing an empty run.
    expect(screen.queryByTestId('analysis-results')).toBeNull()
  })

  it('asks for parameter values in the body, not in a modal', () => {
    render(
      <ResultsBody
        controller={controller({
          parameters: { hasParameters: true, isOpen: true },
        })}
        prompts="inline"
      />
    )

    expect(screen.getByTestId('parameter-form').dataset.presentation).toBe(
      'inline'
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('pre-run steps on the results page', () => {
  it('keeps the consent dialog, which has no drawer to cover', () => {
    render(
      <ResultsBody controller={controller({ consent: { isOpen: true } })} />
    )

    expect(screen.queryByTestId('analyze-consent-inline')).toBeNull()
    expect(screen.getByText('Run EXPLAIN ANALYZE?')).toBeTruthy()
  })

  it('keeps the parameter dialog modal', () => {
    render(
      <ResultsBody
        controller={controller({
          parameters: { hasParameters: true, isOpen: true },
        })}
      />
    )

    expect(screen.getByTestId('parameter-form').dataset.presentation).toBe(
      'modal'
    )
  })
})
