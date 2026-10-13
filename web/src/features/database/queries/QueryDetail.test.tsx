import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { CA_KEYS, CA_NOW, caFixtures } from '@/lib/monitor/fixturesCa'
import { renderWithClient } from '@/test-utils'
import { stepsForPhase } from '../analyze/analyzingSteps'
import { provenance, QueryDetail, readysetLine } from './QueryDetail'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({ useNavigate: () => navigate })
})

const demo = caFixtures('ca-database')
const query = demo.query('demo', CA_KEYS.postsByOwneruserid)!
const healthy = demo.query('demo', CA_KEYS.postsById)!

beforeEach(() => {
  setFixtureTiming(0)
  setFixtureOverride('ca-database')
})
afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
  navigate.mockReset()
})

const actions = {
  range: '1h' as const,
  onClose: vi.fn(),
  onAnalyzeAgain: vi.fn(),
  onBenchmark: vi.fn(),
}

describe('QueryDetail', () => {
  it('reads the row, the two histories, the fix with its provenance, the query and Readyset', async () => {
    renderWithClient(
      <QueryDetail target="demo" query={query} now={CA_NOW} {...actions} />
    )
    expect(
      screen.getByRole('heading', {
        level: 2,
        name: 'SELECT on posts by owneruserid',
      })
    ).toBeTruthy()
    expect(screen.getByText('Fix suggested')).toBeTruthy()
    expect(screen.getByText('0.62 calls/s')).toBeTruthy()
    expect(screen.getAllByText('65.4 ms')).toHaveLength(2)
    expect(screen.getByText('17% of time')).toBeTruthy()
    expect(screen.getByText('Mean latency')).toBeTruthy()
    expect(screen.getByText('Calls per second')).toBeTruthy()
    expect(screen.getByTestId('fix-sql').textContent).toContain(
      'CREATE INDEX CONCURRENTLY idx_posts_owneruserid_creationdate'
    )
    expect(screen.getByRole('button', { name: 'Copy fix' })).toBeTruthy()
    expect(screen.getByText('Builds without blocking writes.')).toBeTruthy()
    expect(
      await screen.findByText(
        'Reads all 245,290 rows to return 583: no index on owneruserid.'
      )
    ).toBeTruthy()
    expect(screen.getByText('Estimated')).toBeTruthy()
    expect(screen.getByText('sampled from table data')).toBeTruthy()
    expect(screen.getByText('analyzed 9 min ago')).toBeTruthy()
    expect(
      screen.getByText('RDST confirms the fix when the index appears.')
    ).toBeTruthy()
    expect(screen.getByTestId('query-sql').textContent).toContain(
      'select id, title from posts'
    )
    expect(screen.getByText('No blockers found, not verified.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Full analysis' })).toBeTruthy()
    const solids = screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
    expect(solids.map((button) => button.textContent)).toEqual(['Copy fix'])
    fireEvent.click(screen.getByRole('button', { name: 'Send to Ask' }))
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/ask' })
    )
  })

  it('shows the analysis for a healthy query', async () => {
    renderWithClient(
      <QueryDetail target="demo" query={healthy} now={CA_NOW} {...actions} />
    )
    expect(screen.getByText('Healthy')).toBeTruthy()
    expect(await screen.findByText('Already efficient')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Copy fix' })).toBeNull()
  })

  it('lists the steps and one Cancel while the analysis runs', () => {
    const onCancel = vi.fn()
    renderWithClient(
      <QueryDetail
        target="demo"
        query={query}
        now={CA_NOW}
        {...actions}
        analyzing={{ steps: stepsForPhase('run'), aboutSeconds: 18 }}
        onCancel={onCancel}
      />
    )
    expect(screen.getAllByText('Analyzing')).toHaveLength(1)
    const block = screen.getByTestId('analyzing-block')
    expect(block.textContent).toContain('Testing rewrites')
    expect(block.textContent).toContain('About 18 s')
    expect(screen.queryByTestId('fix-sql')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
  })

  it("shows and copies the index plan's statement when the query has an open action", async () => {
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const plan =
      'CREATE INDEX CONCURRENTLY idx_posts_owneruserid ON posts (owneruserid);'
    renderWithClient(
      <QueryDetail
        target="demo"
        query={query}
        now={CA_NOW}
        {...actions}
        planSql={plan}
      />
    )
    expect(screen.getByTestId('fix-sql').textContent).toBe(plan)
    // The analysis prose stays.
    expect(
      await screen.findByText(
        'Reads all 245,290 rows to return 583: no index on owneruserid.'
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Copy fix' }))
    expect(writeText).toHaveBeenCalledWith(plan)
  })

  it('offers Analyze on a query never analyzed, Analyze again after one', () => {
    const { rerender } = renderWithClient(
      <QueryDetail
        target="demo"
        query={{ ...query, last_analysis: null }}
        now={CA_NOW}
        {...actions}
      />
    )
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeTruthy()
    rerender(
      <QueryDetail target="demo" query={query} now={CA_NOW} {...actions} />
    )
    expect(screen.getByRole('button', { name: 'Analyze again' })).toBeTruthy()
  })

  it('says Waiting for sign-in for an analysis held for one', () => {
    renderWithClient(
      <QueryDetail
        target="demo"
        query={{
          ...query,
          status: 'analyzing',
          last_analysis: {
            state: 'queued',
            finished_at: null,
            analysis_ref: null,
            mode: '',
            param_source: null,
            reason: 'Sign in to turn on AI monitoring',
            queue_state: 'held',
            held_reason: 'sign_in',
          },
        }}
        now={CA_NOW}
        {...actions}
      />
    )
    expect(screen.getByText('Waiting for sign-in')).toBeTruthy()
    expect(screen.queryByText('Analyzing')).toBeNull()
  })

  it('says why an analysis is on hold, with Analyze now', () => {
    const onAnalyzeAgain = vi.fn()
    renderWithClient(
      <QueryDetail
        target="demo"
        query={{
          ...query,
          status: 'analyzing',
          last_analysis: {
            state: 'queued',
            finished_at: null,
            analysis_ref: null,
            mode: '',
            param_source: null,
            reason: null,
            queue_state: 'held',
            held_reason: 'ai_paused',
          },
        }}
        job={
          {
            id: 7,
            query_key: query.key,
            reason: 'new',
            state: 'queued',
            queue_state: 'held',
            held_reason: 'ai_paused',
            reason_text:
              "AI monitoring is paused. Check this database's AI monitoring status.",
          } as never
        }
        now={CA_NOW}
        {...actions}
        onAnalyzeAgain={onAnalyzeAgain}
      />
    )
    expect(screen.getByText('On hold')).toBeTruthy()
    expect(screen.getByTestId('analysis-line').textContent).toContain(
      "On hold: AI monitoring is paused. Check this database's AI monitoring status."
    )
    fireEvent.click(screen.getByRole('button', { name: 'Analyze now' }))
    expect(onAnalyzeAgain).toHaveBeenCalled()
  })

  it('says why the last analysis failed, with Retry', () => {
    const onAnalyzeAgain = vi.fn()
    renderWithClient(
      <QueryDetail
        target="demo"
        query={{
          ...query,
          status: 'not_analyzed',
          suggested_fix: null,
          last_analysis: {
            state: 'failed',
            finished_at: CA_NOW - 60,
            analysis_ref: null,
            mode: '',
            param_source: null,
            reason: 'EXPLAIN timed out after 30 s',
            error_kind: 'timeout',
          },
        }}
        now={CA_NOW}
        {...actions}
        onAnalyzeAgain={onAnalyzeAgain}
      />
    )
    expect(screen.getByText('Failed')).toBeTruthy()
    expect(screen.getByTestId('analysis-line').textContent).toContain(
      "The last analysis didn't finish. EXPLAIN timed out after 30 s."
    )
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onAnalyzeAgain).toHaveBeenCalled()
  })

  it('hides Analyze for a statement RDST never analyzes and says why', () => {
    renderWithClient(
      <QueryDetail
        target="demo"
        query={{
          ...query,
          text: 'UPDATE posts SET score = score + 1 WHERE id = $1',
          analyzable: undefined,
          status: 'not_analyzed',
          suggested_fix: null,
          last_analysis: null,
        }}
        now={CA_NOW}
        {...actions}
      />
    )
    expect(screen.queryByRole('button', { name: 'Analyze' })).toBeNull()
    expect(screen.getByTestId('not-analyzed-line').textContent).toBe(
      "No deep analysis. RDST doesn't analyze UPDATE statements because they change data."
    )
  })

  it('estimates the fix gain for a query with a fix, and only then', () => {
    const onBenchmark = vi.fn()
    const { rerender } = renderWithClient(
      <QueryDetail
        target="demo"
        query={query}
        now={CA_NOW}
        {...actions}
        onBenchmark={onBenchmark}
      />
    )
    expect(screen.queryByRole('button', { name: 'Benchmark' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Estimate fix gain' }))
    expect(onBenchmark).toHaveBeenCalled()
    rerender(
      <QueryDetail target="demo" query={healthy} now={CA_NOW} {...actions} />
    )
    expect(
      screen.queryByRole('button', { name: 'Estimate fix gain' })
    ).toBeNull()
  })

  it('tags an analysis as estimated or measured', () => {
    const digest = demo.queryAnalysis('demo', CA_KEYS.postsByOwneruserid)!
    expect(provenance(digest)).toEqual({
      tag: 'Estimated',
      reason: 'sampled from table data',
    })
    expect(
      provenance({ ...digest, param_source: null, params: [] }).reason
    ).toBe('planner only, no sampled values')
    expect(provenance({ ...digest, mode: 'analyze' }).tag).toBe('Measured')
    expect(
      provenance({ ...digest, mode: 'analyze', param_source: 'sampled' }).reason
    ).toBe('from sampled values')
    expect(readysetLine(digest)).toBe('No blockers found, not verified.')
    expect(readysetLine(undefined)).toBe(
      'Checked for cache blockers after an analysis.'
    )
    expect(readysetLine(undefined, false)).toBe(
      'Not checked yet. Analyze checks it for cache blockers.'
    )
    expect(
      provenance({
        ...digest,
        param_source: null,
        params: [{ name: '$1', value: '661' }],
      }).reason
    ).toBe('planner, with sampled values')
  })
})
