import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { inboxItems } from '@/lib/monitor/fixtures'
import { renderWithClient } from '@/test-utils'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ to, children }: { to: string; children?: ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))

import { InboxDetail, InboxDetailEmpty } from './InboxDetail'
import { useInboxActions } from './useInboxActions'

const NOW = 1_790_611_200

function Harness({ item }: { item: ReturnType<typeof inboxItems>[number] }) {
  const actions = useInboxActions()
  return <InboxDetail item={item} now={NOW} actions={actions} />
}

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
})

it('reads an older item without the redesign fields: its summary, advice and link', () => {
  const base = inboxItems(NOW).find((item) => item.type === 'stuck_query')
  const item = {
    ...(base as NonNullable<typeof base>),
    kind: undefined,
    band: undefined,
    advice: {
      summary: 'A query has been running for a long time.',
      steps: [
        'Cancel it: SELECT pg_cancel_backend(<pid>);',
        'Check the locks it holds',
      ],
    },
  }
  renderWithClient(<Harness item={item} />)
  const detail = screen.getByRole('region', { name: item.title })
  expect(within(detail).getByText('Act now')).toBeTruthy()
  const steps = within(detail).getByRole('region', { name: 'What to do' })
  expect(within(steps).getByText('Cancel it')).toBeTruthy()
  expect(
    within(steps).getByText('SELECT pg_cancel_backend(<pid>);')
  ).toBeTruthy()
  expect(within(steps).getAllByRole('button', { name: 'Copy' }).length).toBe(1)
  expect(within(detail).getByRole('button', { name: 'Resolve' })).toBeTruthy()
  expect(within(detail).getByRole('link', { name: /Open/ })).toBeTruthy()
})

it('keeps the header buttons whole in a narrow pane: the meta truncates, the buttons wrap', () => {
  const item = inboxItems(NOW).find((entry) => entry.state === 'resolved')
  renderWithClient(<Harness item={item as NonNullable<typeof item>} />)
  const header = screen.getByTestId('inbox-detail-header')
  expect(header.className).toContain('flex-wrap')
  expect(header.className).not.toMatch(/(^| )h-15( |$)/)
  const meta = header.children[1] as HTMLElement
  expect(meta.className).toContain('truncate')
  expect(meta.className).toContain('min-w-0')
  const buttons = within(header).getByRole('button', { name: 'Reopen' })
    .parentElement as HTMLElement
  expect(buttons.className).toContain('flex-wrap')
  expect(buttons.className).not.toContain('shrink-0')
})

it('shows a closed older item with its place in cream, Reopen and no steps', () => {
  const base = inboxItems(NOW).find((item) => item.state === 'resolved')
  const item = {
    ...(base as NonNullable<typeof base>),
    kind: undefined,
    band: undefined,
  }
  renderWithClient(<Harness item={item} />)
  const detail = screen.getByRole('region', { name: item.title })
  expect(within(detail).getByText('Closed')).toBeTruthy()
  expect(within(detail).queryByRole('button', { name: 'Resolve' })).toBeNull()
  expect(within(detail).getByRole('button', { name: 'Reopen' })).toBeTruthy()
  expect(
    within(detail).queryByRole('region', { name: 'What to do' })
  ).toBeNull()
  expect(within(detail).getByRole('link', { name: /^Open/ })).toBeTruthy()
})

it('lists the kebab and mute items without icons', async () => {
  const item = inboxItems(NOW).find((entry) => entry.type === 'stuck_query')
  renderWithClient(<Harness item={item as NonNullable<typeof item>} />)
  fireEvent.keyDown(
    screen.getByRole('button', { name: `More actions for ${item?.title}` }),
    { key: 'ArrowDown' }
  )
  const resolved = await screen.findByRole('menuitem', {
    name: 'Mark resolved',
  })
  const items = [resolved, screen.getByRole('menuitem', { name: 'Mute rules' })]
  for (const entry of items) expect(entry.querySelector('svg')).toBeNull()
})

it('links an open item to its likely fix on Actions', () => {
  const base = inboxItems(NOW).find((entry) => entry.type === 'stuck_query')
  const item = {
    ...(base as NonNullable<typeof base>),
    action: {
      id: 42,
      title: 'Add an index on orders (customer_id)',
      type: 'add_index',
    },
  }
  renderWithClient(<Harness item={item} />)
  const line = screen.getByTestId('item-likely-fix')
  expect(line.textContent).toBe(`Likely fix: ${item.action.title}`)
  const href = within(line).getByRole('link').getAttribute('href')
  expect(href).toBe(`/actions?db=${encodeURIComponent(item.target)}&action=42`)
})

it.each([
  ['connections_near_max', 'Raise max_connections'],
  ['lock_contention', 'Set lock_timeout'],
  ['cache_hit_ratio_drop', 'Raise shared_buffers'],
])('shows the likely fix on a database-wide %s item', (type, title) => {
  const base = inboxItems(NOW).find(
    (entry) => entry.type === 'rollback_or_deadlock_spike'
  )
  const item = {
    ...(base as NonNullable<typeof base>),
    type,
    subject: 'database',
    query_keys: [],
    action: { id: 7, title, type: 'change_setting' },
  }
  renderWithClient(<Harness item={item} />)
  const line = screen.getByTestId('item-likely-fix')
  expect(line.textContent).toBe(`Likely fix: ${title}`)
  expect(within(line).getByRole('link').getAttribute('href')).toBe(
    `/actions?db=${encodeURIComponent(item.target)}&action=7`
  )
})

it('shows no likely fix on a closed item', () => {
  const base = inboxItems(NOW).find((entry) => entry.state === 'resolved')
  const item = {
    ...(base as NonNullable<typeof base>),
    action: { id: 42, title: 'Add an index', type: 'add_index' },
  }
  renderWithClient(<Harness item={item} />)
  expect(screen.queryByTestId('item-likely-fix')).toBeNull()
})

it('leads the daily report notice with what the report found', () => {
  const item = inboxItems(NOW).find((item) => item.type === 'daily_summary')
  renderWithClient(<Harness item={item as NonNullable<typeof item>} />)
  const detail = screen.getByRole('region', { name: item?.title })
  expect(within(detail).getByText(item?.summary ?? '')).toBeTruthy()
})

it('opens the mute rules from the item menu, each with Unmute', async () => {
  const item = inboxItems(NOW).find((entry) => entry.type === 'stuck_query')
  renderWithClient(<Harness item={item as NonNullable<typeof item>} />)
  fireEvent.keyDown(
    screen.getByRole('button', { name: `More actions for ${item?.title}` }),
    { key: 'ArrowDown' }
  )
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Mute rules' }))
  const dialog = await screen.findByRole('dialog', { name: 'Mute rules' })
  expect(
    (await within(dialog).findAllByRole('button', { name: 'Unmute' })).length
  ).toBe(3)
})

it('drops the pick hint over an empty list and counts the Closed view', () => {
  renderWithClient(
    <InboxDetailEmpty actNow={0} watch={1} closed={2} listEmpty />
  )
  expect(screen.getByText('2 items closed in the last 7 days.')).toBeTruthy()
  expect(screen.queryByText(/Pick one on the left/)).toBeNull()
})

it('offers Add a database in the empty pane when there is no database', () => {
  const add = vi.fn()
  renderWithClient(
    <InboxDetailEmpty actNow={0} watch={0} onAddDatabase={add} />
  )
  expect(screen.getByText('No database yet')).toBeTruthy()
  expect(screen.queryByText(/Pick one on the left/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Add a database' }))
  expect(add).toHaveBeenCalledOnce()
})

it('says how many changes wait when none is Do first', () => {
  const base = inboxItems(NOW).find((entry) => entry.type === 'changes_ready')
  const item = base as NonNullable<typeof base>
  renderWithClient(
    <Harness
      item={{
        ...item,
        changes: {
          count: 3,
          do_first: 0,
          titles: ['Add an index'],
          action_ids: [1],
        },
      }}
    />
  )
  expect(screen.getByText('3 to do, none urgent')).toBeTruthy()
})
