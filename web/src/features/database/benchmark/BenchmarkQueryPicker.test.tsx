import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it } from 'vitest'
import type { LoadTestController } from '@/features/caching/load-test/useLoadTestController'
import { BenchmarkQueryPicker } from './BenchmarkQueryPicker'

afterEach(cleanup)

function Picker() {
  const [selected, setSelected] = useState(['a'])
  const controller = {
    queries: [
      { hash: 'a', tag: 'Orders', sql: 'SELECT 1', source: 'saved' },
      {
        hash: 'b',
        tag: '',
        question: 'Customers',
        sql: 'SELECT 2',
        source: 'monitor-benchmark',
      },
      { hash: 'c', tag: 'Delete', sql: 'DELETE FROM t', source: 'saved' },
    ],
    selectedCount: selected.length,
    selectedQueries: selected,
    queriesWithParameters: [],
    toggleQuery: (id: string) =>
      setSelected((previous) =>
        previous.includes(id)
          ? previous.filter((item) => item !== id)
          : [...previous, id]
      ),
  } as unknown as LoadTestController
  return <BenchmarkQueryPicker controller={controller} />
}

it('selects saved and monitored queries and keeps selections while searching', () => {
  render(<Picker />)
  fireEvent.click(screen.getByRole('button', { name: 'Select Customers' }))
  expect(screen.getByText('Pick queries (2 selected)')).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Orders' } })
  expect(screen.getByText('Pick queries (2 selected)')).toBeTruthy()
  expect(
    screen.queryByRole('button', { name: 'Deselect Customers' })
  ).toBeNull()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })
  expect(
    screen
      .getByRole('button', { name: 'Deselect Customers' })
      .getAttribute('aria-pressed')
  ).toBe('true')
  expect(
    screen
      .getByRole('button', { name: 'Select Delete' })
      .getAttribute('aria-disabled')
  ).toBe('true')
})
