import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionCard, ActionsPage, IndexPlan } from '@/lib/monitor/actions'
import { changeCount, planSqlFor } from './actionsModel'
import { FirstPlanPointer } from './FirstPlanPointer'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock()
})

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
    priority: 0,
    jev: 0.5,
    severity: 'high',
    category: 'settings',
    ...overrides,
  } as ActionCard
}

function page(overrides: Partial<ActionsPage> = {}): ActionsPage {
  const index = card({
    id: 2,
    key: 'add:orders',
    type: 'add_index',
    category: 'indexes',
    title: 'Add an index on orders(customer_id)',
  })
  return {
    target: 'db',
    computed_at: 100,
    card_count: 2,
    change_count: 2,
    checkup: {
      state: 'done',
      analyzed: 0,
      total: 0,
      due_by: null,
      report_id: 1,
      finished_at: 100,
    },
    actions: [card({}), index],
    more: [],
    open_count: 2,
    recently_done: [],
    snoozed: 0,
    dismissed: 0,
    set_aside: [],
    coverage: {},
    sections: {},
    window: { observed_s: 7200, counter_age_s: 7200 },
    ...overrides,
  }
}

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

  it('asks for a sign-in when the first analyses wait for one', () => {
    const open = vi.fn()
    render(
      <FirstPlanPointer
        page={{
          ...building,
          checkup: {
            ...building.checkup,
            held: 'sign_in',
            held_text: 'Sign in to turn on AI monitoring',
          },
        }}
        seen={false}
        onOpenActions={open}
      />
    )
    const pointer = screen.getByTestId('first-plan-pointer')
    expect(screen.getByText('Sign in to run the first analyses')).toBeTruthy()
    expect(pointer.textContent).toContain(
      'AI checks need a Readyset sign-in, even with your own key.'
    )
    expect(pointer.textContent).not.toContain('Building your first plan')
    expect(pointer.textContent).not.toContain('of 10 done')
    const link = screen.getByRole('link', { name: 'Sign in' })
    expect(link.getAttribute('href')).toBe('/account-login')
    expect(link.getAttribute('title')).toBe('Sign in to turn on AI monitoring')
    expect(screen.queryByRole('button', { name: 'Go to Actions' })).toBeNull()
  })

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

describe('changeCount', () => {
  it("counts the index plan's changes and every other kind's", () => {
    const plan = { add: [{}, {}], drop: [{}] } as never
    expect(changeCount(page(), plan)).toBe(4)
    expect(changeCount(page())).toBe(2)
  })
})

describe('planSqlFor', () => {
  const add = (id: string, sql: string, helps: [string, number][]) => ({
    id,
    sql,
    helps: helps.map(([key, gain]) => ({ key, gain })),
  })
  const plan = {
    add: [
      add('a', 'CREATE INDEX a ON t (x);', [['q1', 0.2]]),
      add('b', 'CREATE INDEX b ON t (x, y);', [
        ['q1', 0.6],
        ['q2', 0.1],
      ]),
      add('c', 'CREATE INDEX c ON u (z);', [['q3', 0.9]]),
    ],
    set_aside: ['c'],
  } as unknown as IndexPlan

  it("gives the plan's statement that helps the query most", () => {
    expect(planSqlFor(plan, 'q1')).toBe('CREATE INDEX b ON t (x, y);')
    expect(planSqlFor(plan, 'q2')).toBe('CREATE INDEX b ON t (x, y);')
  })

  it('gives nothing for a query without an open action', () => {
    expect(planSqlFor(plan, 'q3')).toBeNull()
    expect(planSqlFor(plan, 'q9')).toBeNull()
    expect(planSqlFor(null, 'q1')).toBeNull()
  })
})
