import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FilterRow, type FilterRowProps } from './FilterRow'

vi.mock('@/lib/monitor/hooks', () => ({
  useConfiguredDatabases: () => ({
    databases: [
      { name: 'demo', state: 'watching', monitored: true, actNow: 1, toDo: 3 },
    ],
    doFirst: null,
    isPending: false,
    isError: false,
  }),
}))

afterEach(cleanup)

const props = (overrides: Partial<FilterRowProps> = {}): FilterRowProps => ({
  scope: {
    value: 'open',
    label: 'Open or closed',
    options: [
      { value: 'open', label: 'Open', count: 3 },
      { value: 'closed', label: 'Closed', count: 7 },
    ],
    onChange: vi.fn(),
  },
  database: { value: null, onChange: vi.fn() },
  ...overrides,
})

describe('FilterRow', () => {
  it('reads the scope as radios with their counts in the quiet tone', () => {
    const onChange = vi.fn()
    const base = props()
    render(<FilterRow {...base} scope={{ ...base.scope, onChange }} />)
    const group = screen.getByRole('radiogroup', { name: 'Open or closed' })
    const open = within(group).getByRole('radio', { name: 'Open 3' })
    expect(open.getAttribute('aria-checked')).toBe('true')
    expect(within(open).getByText('3').className).toContain(
      'text-content-layout-3'
    )
    fireEvent.click(within(group).getByRole('radio', { name: 'Closed 7' }))
    expect(onChange).toHaveBeenCalledWith('closed')
  })

  it('carries the database filter, with no kinds or sort unless asked', () => {
    render(<FilterRow {...props()} />)
    const row = screen.getByRole('group', { name: 'Filters' })
    expect(
      within(row).getByRole('button', { name: 'Database: All databases' })
    ).toBeTruthy()
    expect(within(row).queryByRole('group', { name: 'Kind' })).toBeNull()
    expect(within(row).queryByRole('button', { name: /^Sort:/ })).toBeNull()
  })

  it('toggles one kind chip at a time, none on meaning all, zero counts dimmed', () => {
    const onChange = vi.fn()
    const kinds = {
      value: 'index' as string | null,
      options: [
        { value: 'index', label: 'Indexes', count: 4 },
        { value: 'setting', label: 'Settings', count: 0 },
      ],
      onChange,
    }
    const { rerender } = render(<FilterRow {...props()} kinds={kinds} />)
    const chips = screen.getByRole('group', { name: 'Kind' })
    const index = within(chips).getByRole('button', { name: 'Indexes 4' })
    const setting = within(chips).getByRole('button', { name: 'Settings 0' })
    expect(index.getAttribute('aria-pressed')).toBe('true')
    expect(setting.getAttribute('aria-pressed')).toBe('false')
    expect(setting.className).toContain('text-content-layout-3')
    fireEvent.click(index)
    expect(onChange).toHaveBeenLastCalledWith(null)
    rerender(<FilterRow {...props()} kinds={{ ...kinds, value: null }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Settings 0' }))
    expect(onChange).toHaveBeenLastCalledWith('setting')
  })

  it('puts the sort last, reading the choice in its label', () => {
    const onChange = vi.fn()
    render(
      <FilterRow
        {...props()}
        sort={{
          value: 'ranked',
          options: [
            { value: 'ranked', label: 'Ranked' },
            { value: 'grouped', label: 'Grouped by database' },
          ],
          onChange,
        }}
      />
    )
    const trigger = screen.getByRole('button', { name: 'Sort: Ranked' })
    const row = screen.getByRole('group', { name: 'Filters' })
    expect(row.lastElementChild?.contains(trigger)).toBe(true)
    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Grouped by database' })
    )
    expect(onChange).toHaveBeenCalledWith('grouped')
  })
})
