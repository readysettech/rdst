import { expect, it } from 'vitest'
import {
  FIXTURE_NOW,
  reportEventsFixture,
  reportEventsRunningFixture,
} from '@/lib/monitor/fixtures'
import type { ReportEvent } from '@/lib/monitor/types'
import { currentStation, overallProgress, stations } from './pipelineModel'
import { applyRunEvent, emptyRunView, foldRunEvents } from './runModel'

it('lays a mid-run snapshot out as five stations with real counts', () => {
  const view = foldRunEvents(reportEventsRunningFixture(FIXTURE_NOW), 0)
  const list = stations(view)
  expect(list.map((station) => station.id)).toEqual([
    'history',
    'select',
    'analyses',
    'health',
    'narrative',
  ])
  expect(list[0].state).toBe('done')
  expect(list[2].detail).toBe('9 of 15')
  expect(currentStation(view)).toBe('analyses')
  const progress = overallProgress(view)
  expect(progress).toBeGreaterThan(0.3)
  expect(progress).toBeLessThan(0.9)
})

it('never goes backwards over a full run and ends at 100%', () => {
  let view = emptyRunView(0)
  let last = 0
  for (const event of reportEventsFixture(FIXTURE_NOW)) {
    view = applyRunEvent(view, event, 0)
    const progress = overallProgress(view)
    expect(progress).toBeGreaterThanOrEqual(last)
    last = progress
  }
  expect(last).toBe(1)
  expect(currentStation(view)).toBeNull()
})

it('uses a streamed history step and summary text when the backend sends them', () => {
  let view = emptyRunView(0)
  view = applyRunEvent(
    view,
    {
      type: 'step',
      step: 'history',
      state: 'running',
      detail: 'Reading 24 h',
    } as ReportEvent,
    0
  )
  expect(stations(view)[0]).toMatchObject({
    state: 'running',
    detail: 'Reading 24 h',
  })
  view = applyRunEvent(
    view,
    { type: 'narrative', delta: 'One query ' } as ReportEvent,
    0
  )
  view = applyRunEvent(
    view,
    { type: 'narrative', delta: 'is 71%.' } as ReportEvent,
    0
  )
  expect(view.narrative).toBe('One query is 71%.')
})
