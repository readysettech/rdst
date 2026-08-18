import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OBSERVED_EVIDENCE_PROVENANCE } from '../../../lib/queryEvidence'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'
import { SavedQueryRow } from './SavedQueryRow'
import type { SavedQueriesController } from './useSavedQueriesController'

afterEach(cleanup)

function entry(
  overrides: Partial<QueryRegistryEntry> = {}
): QueryRegistryEntry {
  return {
    hash: 'abc1234567890',
    sql: 'SELECT * FROM users WHERE id = 1',
    tag: '',
    source: 'top',
    target: 'demo',
    frequency: 3,
    last_analyzed: '',
    observation_count: 100,
    avg_duration_ms: 12,
    ...overrides,
  }
}

function makeState() {
  return {
    expandedHash: null,
    highlightedHash: null,
    confirmingHash: null,
    editingHash: null,
    editingSqlHash: null,
    tagDraft: '',
    sqlDraft: '',
  } as unknown as SavedQueriesController['rowState']
}

function makeActions() {
  return {
    isCached: () => false,
    cacheRunFor: () => undefined,
    analyze: vi.fn(),
    cacheQuery: vi.fn(),
    runTest: vi.fn(),
    toggleExpanded: vi.fn(),
    startEditSql: vi.fn(),
    startRename: vi.fn(),
    markReviewed: vi.fn(),
    confirmDelete: vi.fn(),
    deleteQuery: vi.fn(),
    cancelDelete: vi.fn(),
    cancelRename: vi.fn(),
    cancelEditSql: vi.fn(),
    saveSql: vi.fn(),
    setTagDraft: vi.fn(),
    setSqlDraft: vi.fn(),
    updateSqlPending: false,
    dismissRun: vi.fn(),
  } as unknown as SavedQueriesController['rowActions']
}

describe('SavedQueryRow last analysis', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('hands the exact query context to analyze from the details view action', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ found: false, analysis: null }),
      })
    )
    const actions = makeActions()
    const analyzed = entry({
      last_analyzed_at: '2026-08-18T08:00:00Z',
      most_recent_params: { p1: '7' },
    })

    render(
      <SavedQueryRow
        entry={analyzed}
        state={{ ...makeState(), expandedHash: analyzed.hash }}
        actions={actions}
        animateEntry={false}
      />
    )

    fireEvent.click(
      await screen.findByRole('button', { name: 'View analysis' })
    )
    expect(actions.analyze).toHaveBeenCalledWith(
      'SELECT * FROM users WHERE id = 1',
      'demo',
      { p1: '7' }
    )
  })
})

describe('SavedQueryRow evidence provenance', () => {
  it('attaches the provenance note to the observed metrics on the card meta line', () => {
    render(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        animateEntry={false}
      />
    )

    const evidence = screen.getByTitle(OBSERVED_EVIDENCE_PROVENANCE)
    expect(evidence.textContent).toContain('100 runs')
    expect(evidence.textContent).toContain('avg 12.0ms')
    // The note covers only the observed evidence, not identity metadata.
    expect(evidence.textContent).not.toContain('hash')
  })

  it('attaches the provenance note to the impact rail in impact mode', () => {
    render(
      <SavedQueryRow
        entry={entry()}
        state={makeState()}
        actions={makeActions()}
        displayMode="card-2"
        animateEntry={false}
      />
    )

    const rail = screen
      .getAllByTitle(OBSERVED_EVIDENCE_PROVENANCE)
      .find((node) => node.textContent?.includes('Observed runs'))
    expect(rail).toBeTruthy()
    expect(rail?.textContent).toContain('Avg latency')
  })
})
