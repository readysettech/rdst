import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConfiguredDatabase } from '@/lib/monitor/hooks'
import { DatabaseFilter } from './DatabaseFilter'

const configured = vi.hoisted(() => ({ databases: [] as ConfiguredDatabase[] }))
vi.mock('@/lib/monitor/hooks', () => ({
  useConfiguredDatabases: () => ({
    databases: configured.databases,
    doFirst: null,
    isPending: false,
    isError: false,
  }),
}))

const db = (
  name: string,
  overrides: Partial<ConfiguredDatabase> = {}
): ConfiguredDatabase => ({
  name,
  state: 'watching',
  monitored: true,
  actNow: 0,
  toDo: 0,
  ...overrides,
})

/** Radix opens its menu from the keyboard in jsdom. */
function open() {
  fireEvent.keyDown(screen.getByTestId('database-filter'), { key: 'Enter' })
}

afterEach(() => {
  cleanup()
  configured.databases = []
})

describe('DatabaseFilter', () => {
  it('reads the choice in its own label', () => {
    const { rerender } = render(
      <DatabaseFilter value={null} onChange={vi.fn()} />
    )
    expect(screen.getByTestId('database-filter').textContent).toBe(
      'All databases'
    )
    rerender(<DatabaseFilter value="demo" onChange={vi.fn()} />)
    expect(screen.getByTestId('database-filter').textContent).toBe('demo')
    expect(screen.getByRole('button', { name: 'Database: demo' })).toBeTruthy()
  })

  it('leaves out All databases when asked, naming the first database instead', async () => {
    configured.databases = [db('demo')]
    render(<DatabaseFilter value={null} onChange={vi.fn()} allOption={false} />)
    expect(screen.getByTestId('database-filter').textContent).toBe('demo')
    open()
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual(['demoWatching0'])
  })

  it('offers All databases, then each database with its state and changes to do', async () => {
    configured.databases = [
      db('demo', { toDo: 5 }),
      db('staging', { state: 'not_monitored', monitored: false, toDo: null }),
    ]
    const onChange = vi.fn()
    render(<DatabaseFilter value="demo" onChange={onChange} />)
    open()
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'All databases',
      'demoWatching5Selected',
      'stagingNot monitored',
    ])
    fireEvent.click(items[0])
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("takes the page's own counts and database list when given", async () => {
    const onChange = vi.fn()
    render(
      <DatabaseFilter
        value={null}
        onChange={onChange}
        databases={[db('demo'), db('analytics')]}
        counts={{ demo: 2 }}
      />
    )
    open()
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'All databasesSelected',
      'demoWatching2',
      'analyticsWatching0',
    ])
    fireEvent.click(items[2])
    expect(onChange).toHaveBeenCalledWith('analytics')
  })
})
