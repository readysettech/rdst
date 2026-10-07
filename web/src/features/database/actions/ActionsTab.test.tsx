import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionCard, ActionsPage, IndexPlan } from '@/lib/monitor/actions'
import { ActionsTab } from './ActionsTab'
import { FirstPlanPointer } from './FirstPlanPointer'

const state = vi.hoisted(() => ({
  page: undefined as ActionsPage | undefined,
  plan: undefined as IndexPlan | null | undefined,
}))
vi.mock('@/lib/monitor/actions', () => ({
  useActions: () => ({ data: state.page, isError: false }),
  useIndexPlan: () => ({ data: state.plan }),
  useActionVerb: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: unknown }) => children,
}))

afterEach(cleanup)

function card(overrides: Partial<ActionCard>): ActionCard {
  return {
    id: 1,
    key: 'k',
    type: 'change_setting',
    state: 'open',
    score: 0.15,
    title: 'Give PostgreSQL more memory for cached data',
    summary: 'Only 42.3% of reads found their data in memory.',
    note: null,
    why: null,
    apply: 'config_restart',
    sql: "ALTER SYSTEM SET shared_buffers = '4GB';",
    restart: true,
    confidence: 'measured',
    queries: [],
    opened_at: 1,
    updated_at: 1,
    snoozed_until: null,
    children: [],
    detail: null,
    window: null,
    steps:
      "Pick a value near 25% of the server's RAM.\nRun the SQL below to set shared_buffers.\nRestart PostgreSQL in a quiet moment.",
    evidence: [],
    rows: [],
    priority: 0,
    jev: 0.5,
    severity: 'high',
    category: 'settings',
    ...overrides,
  } as ActionCard
}

function page(overrides: Partial<ActionsPage> = {}): ActionsPage {
  const setting = card({})
  const index = card({
    id: 2,
    key: 'add:orders',
    type: 'add_index',
    title: 'Add an index on orders(customer_id)',
    summary: 'Should speed up 1 query',
    severity: 'low',
    category: 'indexes',
    steps: null,
    sql: 'CREATE INDEX CONCURRENTLY idx ON orders (customer_id);',
  })
  return {
    target: 'db',
    computed_at: 100,
    card_count: 2,
    checkup: {
      state: 'done',
      analyzed: 0,
      total: 0,
      due_by: null,
      report_id: 1,
      finished_at: 100,
    },
    actions: [setting, index],
    more: [],
    open_count: 2,
    recently_done: [],
    snoozed: 0,
    dismissed: 0,
    set_aside: [],
    window: { observed_s: 7200, counter_age_s: 7200 },
    sections: {
      settings: {
        headline: 'Change 1 server setting',
        lead: ['Give PostgreSQL more memory for cached data.'],
        details: ['RDST compared the server settings with what it measured.'],
        facts: [],
      },
      indexes: {
        headline: 'Add 1 index',
        lead: ['Add 1 index on orders to speed up 1 query.'],
        details: ['Reads took 80% of the time and writes 20%.'],
        facts: [{ label: 'Writes', value: '20% of time' }],
      },
    },
    coverage: {
      indexes: {
        state: 'found',
        checked_at: 90,
        note: 'Weighed queries',
        next_at: null,
      },
      settings: {
        state: 'found',
        checked_at: 90,
        note: 'Compared settings',
        next_at: null,
      },
      maintenance: {
        state: 'checked',
        checked_at: 90,
        note: 'Checked dead rows, stale statistics and transaction age',
        next_at: Date.now() / 1000 + 12 * 60 - 5,
      },
      rewrites: {
        state: 'checked',
        checked_at: 90,
        note: 'Tested rewrites',
        next_at: null,
      },
    },
    ...overrides,
  } as ActionsPage
}

const PLAN = {
  engine: 'postgresql',
  computed_at: 100,
  observed_s: 7200,
  counter_age_s: 7200,
  keep: [],
  add: [
    {
      id: 'add:orders',
      table: 'orders',
      columns: ['customer_id'],
      roles: ['equality'],
      replaces: [],
      method: 'btree',
      merged: [],
      helps: [
        {
          key: 'q1',
          name: 'SELECT on orders by customer_id',
          share: 0.4,
          gain: 0.8,
          verified: true,
          gain_pct: 80,
          impact: null,
        },
      ],
      benefit: 0.32,
      verified: true,
      write_cost: {
        writes_per_s: 0.4,
        peak_writes_per_s: 1,
        existing_indexes: 1,
        updated_columns: [],
        hot_risk: false,
      },
      label: 'safe',
      label_reason: 'cheap',
      sql: 'CREATE INDEX CONCURRENTLY idx ON orders (customer_id);',
      undo_sql: 'DROP INDEX CONCURRENTLY public.idx;',
    },
  ],
  drop: [],
  skipped: [],
  later: [],
  covered: [],
  defeated: [],
  set_aside: [],
  sql: 'CREATE INDEX CONCURRENTLY idx ON orders (customer_id);',
  rollback_sql: 'DROP INDEX CONCURRENTLY public.idx;',
} as unknown as IndexPlan

function renderTab(section: 'all' | 'settings' | 'indexes' = 'all') {
  state.plan = state.plan ?? PLAN
  const onSectionChange = vi.fn()
  render(
    <ActionsTab
      target="db"
      section={section}
      onSectionChange={onSectionChange}
      onShowQuery={vi.fn()}
      onOpenQueries={vi.fn()}
    />
  )
  return onSectionChange
}

describe('ActionsTab', () => {
  it('says the plan is coming together once every first-pass analysis is in', () => {
    state.page = page({
      actions: [],
      more: [],
      card_count: 0,
      open_count: 0,
      checkup: {
        state: 'first_pass',
        analyzed: 11,
        total: 11,
        due_by: null,
        report_id: null,
        finished_at: null,
      },
      coverage: {
        indexes: {
          state: 'checking',
          checked_at: null,
          note: 'x',
          next_at: null,
        },
        settings: {
          state: 'checking',
          checked_at: null,
          note: 'x',
          next_at: null,
        },
        maintenance: {
          state: 'checking',
          checked_at: null,
          note: 'x',
          next_at: null,
        },
        rewrites: {
          state: 'waiting',
          checked_at: null,
          note: 'x',
          next_at: null,
        },
      },
    } as Partial<ActionsPage>)
    renderTab()
    expect(screen.getByTestId('actions-building').textContent).toContain(
      'Every busy query is analyzed. The plan is coming together.'
    )
    // Every kind shows it's in progress, and the open one says so too.
    const cards = within(screen.getByTestId('action-sections')).getAllByRole(
      'tab'
    )
    expect(cards.map((card) => card.getAttribute('data-state'))).toEqual([
      'checking',
      'checking',
      'checking',
      'waiting',
    ])
    expect(screen.getByTestId('actions-pending').textContent).toContain(
      'Checking now'
    )
  })

  it('always shows the four kinds of change, opening on the most pressing', () => {
    state.page = page()
    state.plan = PLAN
    const onSectionChange = renderTab()
    const cards = within(screen.getByTestId('action-sections')).getAllByRole(
      'tab'
    )
    expect(cards.map((card) => card.getAttribute('data-state'))).toEqual([
      'found',
      'found',
      'checked',
      'checked',
    ])
    expect(cards[0].textContent).toContain('1 to add')
    expect(cards[1].textContent).toContain('1 high impact')
    expect(cards[2].textContent).toContain('Nothing to change')
    expect(cards[1].getAttribute('aria-selected')).toBe('true')
    fireEvent.click(cards[0])
    expect(onSectionChange).toHaveBeenCalledWith('indexes')
  })

  it('says what was checked and when the next check is, for a kind with nothing to change', () => {
    state.page = page()
    renderTab('maintenance' as 'settings')
    expect(screen.getByTestId('actions-all-clear').textContent).toContain(
      'Checked dead rows, stale statistics and transaction age. Next check in 12 min.'
    )
  })

  it('lists dismissed changes so one can be brought back', () => {
    state.page = page({
      set_aside: [card({ id: 7, key: 'gone', state: 'dismissed' })],
    })
    renderTab('settings')
    const aside = screen.getByTestId('actions-set-aside')
    expect(aside.textContent).toContain('Set aside (1)')
    fireEvent.click(within(aside).getByRole('button', { name: /Set aside/ }))
    expect(aside.textContent).toContain('Dismissed')
    expect(
      within(aside).getByRole('button', { name: 'Bring back' })
    ).toBeTruthy()
  })

  it('leads several changes with what to do and keeps the numbers behind a disclosure', () => {
    const memory = card({})
    const sorts = card({ id: 3, key: 'k3', title: 'Raise work_mem' })
    state.page = page({ actions: [memory, sorts] })
    renderTab('settings')
    const story = screen.getByTestId('action-story')
    expect(story.textContent).toContain(
      'Give PostgreSQL more memory for cached data.'
    )
    expect(story.textContent).not.toContain('RDST compared the server settings')
    fireEvent.click(
      within(story).getByRole('button', { name: 'Show the numbers' })
    )
    expect(story.textContent).toContain(
      'RDST compared the server settings with what it measured.'
    )
  })

  it('opens a lone change on its steps, with no summary repeating it', () => {
    state.page = page()
    renderTab('settings')
    expect(screen.queryByTestId('action-story')).toBeNull()
    const detail = screen.getByTestId('action-detail')
    expect(
      within(detail)
        .getAllByRole('listitem')
        .map((item) => item.textContent)
    ).toEqual([
      "Pick a value near 25% of the server's RAM.",
      'Run the SQL below to set shared_buffers.',
      'Restart PostgreSQL in a quiet moment.',
    ])
    expect(detail.textContent).toContain(
      "ALTER SYSTEM SET shared_buffers = '4GB';"
    )
  })

  it('lists each index by table and shape, and rolls the plan back', () => {
    state.page = page()
    state.plan = PLAN
    renderTab('indexes')
    const row = screen.getByTestId('index-plan-add')
    expect(row.textContent).toContain('orders')
    expect(row.textContent).toContain('customer_id')
    expect(row.textContent).toContain('Single column')
    expect(row.textContent).toContain('Speeds up 1 query, 40% of database time')
    fireEvent.click(screen.getByRole('button', { name: 'Show the SQL' }))
    expect(screen.getByTestId('apply-plan').textContent).toContain(
      'DROP INDEX CONCURRENTLY public.idx;'
    )
    fireEvent.click(within(row).getAllByRole('button')[0])
    expect(row.textContent).toContain(
      'Add an index on orders (customer_id) to speed up SELECT on orders by customer_id.'
    )
    expect(within(row).getByRole('button', { name: 'Dismiss' })).toBeTruthy()
    expect(row.textContent).toContain('SELECT on orders by customer_id')
  })
  it('leads a drop with the writes it speeds up', () => {
    state.page = page()
    state.plan = {
      ...PLAN,
      add: [],
      drop: [
        {
          id: 'drop:x',
          table: 'readings',
          schema: 'public',
          index_name: 'readings_value_idx',
          columns: ['value'],
          reason: 'unused',
          covered_by: null,
          size_bytes: 16 * 1024 * 1024,
          scans: 0,
          last_scan_at: null,
          evidence_age_s: 17 * 3600,
          confidence: 'high',
          table_writes_per_s: 182,
          replica_caveat: false,
          slows: [
            {
              key: 'w1',
              name: 'INSERT on readings',
              verb: 'insert',
              share: 0.12,
              calls_per_s: 182,
            },
          ],
          sql: 'DROP INDEX CONCURRENTLY public.readings_value_idx;',
          restore_sql: null,
        },
      ],
    } as unknown as IndexPlan
    renderTab('indexes')
    const row = screen.getByTestId('index-plan-drop')
    expect(row.textContent).toContain('Speeds up 1 write, 12% of database time')
    fireEvent.click(within(row).getAllByRole('button')[0])
    expect(row.textContent).toContain(
      'Drop readings_value_idx on readings to speed up INSERT on readings.'
    )
    expect(row.textContent).toContain('182 runs a second, 12% of database time')
    expect(row.textContent).toContain(
      'RDST measures these writes before and after'
    )
  })
})

describe('FirstPlanPointer', () => {
  const building = page({
    actions: [],
    card_count: 0,
    checkup: {
      state: 'first_pass',
      analyzed: 3,
      total: 10,
      due_by: null,
      report_id: null,
      finished_at: null,
    },
  })

  it('points at Actions while the first plan is built, then once it is ready', () => {
    const open = vi.fn()
    const { rerender } = render(
      <FirstPlanPointer page={building} seen={false} onOpenActions={open} />
    )
    expect(screen.getByText('Building your first plan')).toBeTruthy()
    expect(screen.getByTestId('first-plan-pointer').textContent).toContain(
      '3 of 10 done'
    )
    rerender(
      <FirstPlanPointer page={page()} seen={false} onOpenActions={open} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'See the actions' }))
    expect(open).toHaveBeenCalled()
    expect(
      screen.getByText('Your first plan is ready: 2 changes worth making')
    ).toBeTruthy()
    rerender(<FirstPlanPointer page={page()} seen onOpenActions={open} />)
    expect(screen.queryByTestId('first-plan-pointer')).toBeNull()
  })
})

describe('FirstPlanPointer on a healthy database', () => {
  it('says the first plan found nothing to change', () => {
    render(
      <FirstPlanPointer
        page={page({ actions: [], more: [], card_count: 0, open_count: 0 })}
        seen={false}
        onOpenActions={vi.fn()}
      />
    )
    expect(
      screen.getByText('Your first plan is in: nothing to change right now')
    ).toBeTruthy()
  })
})
