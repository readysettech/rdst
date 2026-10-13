import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { at } from '@/lib/monitor/fixturesCa'
import {
  FirstRunMore,
  FirstRunNote,
  FirstRunStrip,
  firstRunLabel,
  firstRunWindow,
  MeasuringQueries,
  moreAsSeen,
  WaitingForQueries,
} from './FirstReadings'
import { type FirstRunStep, firstRun } from './firstReadingsModel'

afterEach(cleanup)

it('spans ten minutes from the first reading, or the readings so far', () => {
  expect(firstRunWindow(at(9, 54), at(9, 56))).toEqual([at(9, 54), at(10, 4)])
  expect(firstRunWindow(at(9, 45), at(9, 56, 30))).toEqual([
    at(9, 45),
    at(9, 56, 30),
  ])
})

it('counts the queries seen but not listed yet', () => {
  expect(moreAsSeen(16, 3)).toBe(13)
  expect(moreAsSeen(3, 3)).toBe(0)
  expect(moreAsSeen(undefined, 3)).toBe(0)
  render(<FirstRunMore seenTotal={16} listed={3} />)
  expect(screen.getByText('13 more as they are seen')).toBeTruthy()
  cleanup()
  render(<FirstRunMore seenTotal={3} listed={3} />)
  expect(screen.queryByText(/more as they are seen/)).toBeNull()
})

it('labels the window by its real span, as the axis draws it', () => {
  expect(firstRunLabel(firstRunWindow(at(9, 54), at(9, 56)), at(9, 56))).toBe(
    'First 10 min'
  )
  const later = firstRunWindow(at(9, 31), at(9, 56))
  expect(firstRunLabel(later, at(9, 56))).toBe('Last 25 min')
})

it('says when the rest arrives under the chart', () => {
  const { rerender, container } = render(<FirstRunNote firstReport />)
  expect(
    screen.getByText('Your first daily report runs tonight, just after midnight.')
  ).toBeTruthy()
  expect(screen.queryByText(/Deep analysis/)).toBeNull()
  rerender(<FirstRunNote firstReport={false} />)
  expect(container.textContent).toBe('')
})

const model = (
  queries: { key: string; analyzable: boolean; assessment: null }[]
) =>
  firstRun({
    now: 1000,
    settings: {
      enabled: true,
      attention_every_min: 5,
      nightly_report: false,
      nightly_hour: 0,
      deep_analysis: true,
    },
    history: null,
    pulses: [],
    queue: null,
    queries,
  })

it.each([
  'off',
  'done',
] as const)('keeps the completed strip honest when analysis is %s', (state) => {
  const done = model([{ key: 'a', analyzable: true, assessment: null }])
  done.steps = done.steps.map(
    (step): FirstRunStep =>
      step.id === 'deep'
        ? {
            ...step,
            state,
            meta: state === 'done' ? '2 analyzed' : 'Turned off',
          }
        : step
  )
  done.complete = true
  render(<FirstRunStrip model={done} now={1000} />)
  expect(screen.getByTestId('first-run').textContent).toContain(
    state === 'off' ? 'Deep analysis is off.' : '2 analyzed.'
  )
})

it('shows the step under way, and every step on Details', () => {
  render(<FirstRunStrip model={model([])} now={1000} />)
  const strip = screen.getByTestId('first-run')
  expect(strip.querySelectorAll('[data-step]')).toHaveLength(1)
  screen.getByRole('button', { name: 'Details' }).click()
})

it('says RDST is connected and waiting before any query runs, and gathering after', () => {
  render(<WaitingForQueries target="shop" model={model([])} now={1000} />)
  expect(screen.getByText('Waiting for queries')).toBeTruthy()
  expect(screen.getByText(/RDST is reading shop/)).toBeTruthy()
  cleanup()
  render(<MeasuringQueries />)
  expect(screen.getByText('RDST is gathering your queries.')).toBeTruthy()
})
