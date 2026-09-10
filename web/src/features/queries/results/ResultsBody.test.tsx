import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ANALYZE_CONSENT_MESSAGE,
  ANALYZE_CONSENT_TITLE,
} from './AnalyzeConsent'
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
  QueryCard: ({ title, meta }: { title?: ReactNode; meta?: ReactNode }) => (
    <div data-testid="query-card">
      <div data-testid="query-card-title">{title}</div>
      <div data-testid="query-card-meta">{meta}</div>
    </div>
  ),
}))

// The chat stack is behind its own chunk; importing this module is what
// "the conversation was opened" means.
const chat = vi.hoisted(() => ({ chunkLoaded: false }))

vi.mock('../../../components/InteractivePanel', () => {
  chat.chunkLoaded = true
  return { InteractivePanel: () => <div data-testid="interactive-panel" /> }
})

function controller(
  overrides: {
    parameters?: Partial<ResultsController['parameters']>
    consent?: Partial<ResultsController['consent']>
    chat?: Partial<ResultsController['chat']>
    analysis?: Partial<ResultsController['analysis']>
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
      ...overrides.analysis,
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
    chat: {
      isOpen: false,
      hasExisting: false,
      results: {},
      ...overrides.chat,
    },
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

  it('carries the warning tone the dialog carries, in the same words', () => {
    render(
      <ResultsBody
        controller={controller({ consent: { isOpen: true } })}
        prompts="inline"
      />
    )

    // Mike #3 / B-05: the drawer used to ask in a neutral card.
    const consent = screen.getByTestId('analyze-consent-inline')
    expect(consent.className).toContain('bg-surface-warning-soft/50')
    expect(consent.className).toContain('border-border-warning-soft')
    expect(consent.className).toContain('shadow-glow-warning')
    expect(screen.getByText(ANALYZE_CONSENT_TITLE)).toBeTruthy()
    expect(screen.getByText(ANALYZE_CONSENT_MESSAGE)).toBeTruthy()
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
    expect(screen.getByText(ANALYZE_CONSENT_TITLE)).toBeTruthy()
    expect(screen.getByText(ANALYZE_CONSENT_MESSAGE)).toBeTruthy()
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

describe('one query block on the analyze screen (B-04)', () => {
  it('calls it the query until a run has been asked for', () => {
    render(<ResultsBody controller={controller()} prompts="inline" />)

    expect(screen.getByTestId('query-card-title').textContent).toBe('Query')
    expect(screen.getByTestId('query-card-meta').textContent).not.toContain(
      'with your values'
    )
  })

  it('leaves the query to the parameter form while values are collected', () => {
    render(
      <ResultsBody
        controller={controller({
          parameters: { hasParameters: true, isOpen: true },
        })}
        prompts="inline"
      />
    )

    expect(screen.getByTestId('parameter-form')).toBeTruthy()
    expect(screen.queryByTestId('query-card')).toBeNull()
  })

  it('names the substituted query once a run exists', () => {
    render(
      <ResultsBody
        controller={controller({
          analysis: { state: 'complete' },
          parameters: { hasParameters: false, isSubstituted: true },
        })}
        prompts="inline"
      />
    )

    expect(screen.getByTestId('query-card-title').textContent).toBe(
      'Analyzed query'
    )
    expect(screen.getByTestId('query-card-meta').textContent).toContain(
      'with your values'
    )
  })
})

describe('the follow-up conversation', () => {
  it('loads the chat only once a question is actually asked', async () => {
    const complete = {
      analysis: {
        state: 'complete',
        results: { query_hash: 'qh1' },
      } as unknown as Partial<ResultsController['analysis']>,
    }

    render(<ResultsBody controller={controller(complete)} />)
    expect(chat.chunkLoaded).toBe(false)
    expect(screen.queryByTestId('interactive-panel')).toBeNull()

    cleanup()
    render(
      <ResultsBody
        controller={controller({ ...complete, chat: { isOpen: true } })}
      />
    )

    expect(await screen.findByTestId('interactive-panel')).toBeTruthy()
    expect(chat.chunkLoaded).toBe(true)
  })
})
