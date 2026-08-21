import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoadTestSetup } from './LoadTestSetup'
import type { LoadTestController } from './useLoadTestController'

vi.mock('@tanstack/react-router', async () => ({
  Link: (await import('@/test-utils')).LinkStub,
  useNavigate: () => vi.fn(),
}))

const PARAM_SQL = 'SELECT id, email\nFROM users\nWHERE status = :status'
const PLAIN_SQL = 'SELECT id\nFROM posts\nORDER BY id'
// getByTitle collapses attribute whitespace before matching.
const PARAM_SQL_TITLE = 'SELECT id, email FROM users WHERE status = :status'
const PLAIN_SQL_TITLE = 'SELECT id FROM posts ORDER BY id'

const PARAM_QUERY = {
  hash: 'with-params',
  tag: 'Users by status',
  sql: PARAM_SQL,
}
const PLAIN_QUERY = { hash: 'no-params', tag: 'All posts', sql: PLAIN_SQL }

function controller(overrides: Record<string, unknown> = {}) {
  return {
    queries: [PARAM_QUERY, PLAIN_QUERY],
    registryLoading: false,
    listError: null,
    refetchRegistry: vi.fn(),
    statusLoading: false,
    statusError: null,
    refetchStatus: vi.fn(),
    destinationTarget: 'demo',
    handleDestinationChange: vi.fn(),
    destinationLock: {
      isResolved: true,
      isLocked: false,
      targetName: 'demo',
      message: '',
      missingTargetRequirements: [],
      keyringAvailable: true,
    },
    connectivity: {
      failure: null,
      isChecking: false,
      ensureReachable: vi.fn().mockResolvedValue(true),
      reset: vi.fn(),
    },
    selectedQueries: [],
    setSelectedQueries: vi.fn(),
    searchTerm: '',
    setSearchTerm: vi.fn(),
    setSourceFilter: vi.fn(),
    testProfile: 'paced',
    setTestProfile: vi.fn(),
    comparative: false,
    setComparative: vi.fn(),
    intervalMs: 100,
    setIntervalMs: vi.fn(),
    capacityClients: 2,
    setCapacityClients: vi.fn(),
    durationSeconds: 30,
    setDurationSeconds: vi.fn(),
    paramValues: {},
    parameterSources: {},
    updateParameter: vi.fn(),
    suggestingParameters: false,
    suggestionMessage: null,
    suggestionSchemaUnavailable: false,
    suggestParameterValues: vi.fn(),
    confirmOpen: false,
    setConfirmOpen: vi.fn(),
    loadSettingsOpen: false,
    setLoadSettingsOpen: vi.fn(),
    sourceOptions: [{ value: 'all', label: 'All sources' }],
    normalizedSourceFilter: 'all',
    targetDetailsError: null,
    refetchTargetDetails: vi.fn(),
    destinationOptions: [{ value: 'demo', label: 'demo' }],
    destinationIsRemote: false,
    filteredQueries: [PARAM_QUERY, PLAIN_QUERY],
    selectedQueryById: new Map(),
    selectedCount: 0,
    runnableCount: 0,
    hiddenSelectedCount: 0,
    queriesWithParameters: [],
    missingParameterCount: 0,
    missingTables: [],
    canStart: false,
    toggleQuery: vi.fn(),
    clearFilters: vi.fn(),
    handleStart: vi.fn(),
    handleConfirmRun: vi.fn(),
    confirmTarget: 'demo',
    confirmIsRemote: false,
    confirmQueryCount: 0,
    confirmLoadSummary: '1 client',
    confirmEstimatedExecutions: 0,
    ...overrides,
  } as unknown as LoadTestController
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LoadTestSetup query rows', () => {
  it('reveals the full SQL beside the parameter inputs of a selected query', () => {
    const selection = {
      query: PARAM_QUERY,
      parameters: [{ placeholder: ':status', index: 1, type: 'named' }],
    }
    render(
      <LoadTestSetup
        controller={controller({
          selectedQueries: ['Users by status'],
          selectedQueryById: new Map([['Users by status', selection]]),
          selectedCount: 1,
          runnableCount: 1,
          queriesWithParameters: [selection],
          missingParameterCount: 1,
        })}
      />
    )

    const code = screen.getByTitle(PARAM_SQL_TITLE)
    expect(code.textContent).toContain('\nFROM users')
    expect(code.textContent).toContain('WHERE status = :status')
    expect(code.className).not.toContain('truncate')
    expect(
      screen.getByPlaceholderText('Enter a representative value')
    ).toBeTruthy()
  })

  it('counts parameters from normalized SQL when original SQL contains literals', () => {
    const query = {
      hash: 'normalized-parameter',
      tag: 'User by id',
      sql: 'SELECT * FROM users WHERE id = :p1',
      original_sql: 'SELECT * FROM users WHERE id = 42',
    }
    render(
      <LoadTestSetup
        controller={controller({
          queries: [query],
          filteredQueries: [query],
        })}
      />
    )

    expect(screen.getByText('1 parameter')).toBeTruthy()
  })

  it('places Suggest values in the run-summary panel, not the footer', () => {
    const selection = {
      query: PARAM_QUERY,
      parameters: [{ placeholder: ':status', index: 1, type: 'named' }],
    }
    render(
      <LoadTestSetup
        controller={controller({
          selectedQueries: ['Users by status'],
          selectedQueryById: new Map([['Users by status', selection]]),
          selectedCount: 1,
          runnableCount: 1,
          queriesWithParameters: [selection],
          missingParameterCount: 1,
          suggestionMessage: 'Suggested 1 value from schema evidence.',
          suggestionSchemaUnavailable: true,
        })}
      />
    )

    const suggest = screen.getByRole('button', { name: 'Suggest values' })
    const advanced = screen.getByRole('button', {
      name: /Advanced load settings/,
    })
    const run = screen.getByRole('button', { name: 'Run load test' })

    // The panel sits under the run-summary rows, before the advanced-settings
    // disclosure, and the summary message with its schema CTA sits with it.
    expect(
      suggest.compareDocumentPosition(advanced) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      suggest.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      screen.getByText('Suggested 1 value from schema evidence.')
    ).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'Initialize the semantic layer' })
    ).toBeTruthy()
  })

  it('omits Suggest values when no selected query has parameters', () => {
    render(<LoadTestSetup controller={controller()} />)

    expect(screen.queryByRole('button', { name: 'Suggest values' })).toBeNull()
  })

  it('formats a selected query without parameters and keeps unselected queries compact', () => {
    const selection = { query: PLAIN_QUERY, parameters: [] }
    render(
      <LoadTestSetup
        controller={controller({
          selectedQueries: ['All posts'],
          selectedQueryById: new Map([['All posts', selection]]),
          selectedCount: 1,
          runnableCount: 1,
        })}
      />
    )

    const plain = screen.getByTitle(PLAIN_SQL_TITLE)
    expect(plain.textContent).toContain('\nFROM posts')
    expect(plain.className).not.toContain('truncate')

    const unselected = screen.getByTitle(PARAM_SQL_TITLE)
    expect(unselected.className).toContain('truncate')
    expect(
      screen.queryByPlaceholderText('Enter a representative value')
    ).toBeNull()
  })
})

describe('LoadTestSetup comparison lane control', () => {
  it('measures the origin alone until the user asks for Readyset', () => {
    const setComparative = vi.fn()
    render(<LoadTestSetup controller={controller({ setComparative })} />)

    const toggle = screen.getByRole('switch', {
      name: 'Compare against Readyset',
    })
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(
      screen.getByText('Origin only. Readyset is skipped for this run.')
    ).toBeTruthy()
    expect(screen.getByText('Origin only', { exact: true })).toBeTruthy()

    toggle.click()
    expect(setComparative).toHaveBeenCalledWith(true)
  })

  it('explains the side-by-side run once the lane is switched on', () => {
    render(<LoadTestSetup controller={controller({ comparative: true })} />)

    const toggle = screen.getByRole('switch', {
      name: 'Compare against Readyset',
    })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    expect(
      screen.getByText('Run against your database and Readyset side by side.')
    ).toBeTruthy()
    expect(screen.getByText('Origin + Readyset', { exact: true })).toBeTruthy()
  })
})

describe('LoadTestSetup empty state (C3)', () => {
  it('offers a Find queries action when no queries are available yet', () => {
    const onFindQueries = vi.fn()
    render(
      <LoadTestSetup
        controller={controller({ queries: [], filteredQueries: [] })}
        onFindQueries={onFindQueries}
      />
    )

    expect(screen.getByText('No queries available yet')).toBeTruthy()
    const button = screen.getByRole('button', { name: 'Find queries' })
    button.click()
    expect(onFindQueries).toHaveBeenCalledTimes(1)
  })

  it('omits the action when no handler is supplied', () => {
    render(
      <LoadTestSetup
        controller={controller({ queries: [], filteredQueries: [] })}
      />
    )

    expect(screen.getByText('No queries available yet')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Find queries' })).toBeNull()
  })
})
