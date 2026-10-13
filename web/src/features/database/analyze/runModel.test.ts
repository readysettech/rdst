import { expect, it } from 'vitest'
import {
  FIXTURE_NOW,
  reportEventsFixture,
  reportEventsRunningFixture,
} from '@/lib/monitor/fixtures'
import {
  activityLabel,
  applyRunEvent,
  elapsedSeconds,
  emptyRunView,
  foldRunEvents,
  progressPercent,
  runCounts,
  runPhase,
} from './runModel'

it('replays a full run from queued to done with every row state', () => {
  const events = reportEventsFixture(FIXTURE_NOW)
  const view = foldRunEvents(events, 1000)
  expect(view.state).toBe('done')
  expect(view.queries).toHaveLength(15)
  const counts = runCounts(view)
  expect(counts.finished).toBe(15)
  expect(counts.skipped).toBeGreaterThan(0)
  expect(counts.failed).toBe(1)
  expect(counts.provisional).toBe(2)
  expect(
    counts.done + counts.skipped + counts.failed + counts.provisional
  ).toBe(15)
  for (const row of view.queries.filter((r) => r.state === 'skipped'))
    expect(row.reason).toBeTruthy()
  expect(view.healthChecks.every((check) => check.state === 'done')).toBe(true)
  expect(Object.values(view.sections).every((s) => s === 'ready')).toBe(true)
  expect(view.budgetMessage).toMatch(/plans only/)
  expect(progressPercent(view)).toBe(100)
  expect(activityLabel(view)).toBe('Report ready')
})

it('walks every intermediate step without losing rows', () => {
  const events = reportEventsFixture(FIXTURE_NOW)
  let view = emptyRunView(0)
  const seen = new Set<string>()
  for (const event of events) {
    view = applyRunEvent(view, event, 0)
    for (const row of view.queries) seen.add(row.state)
    if (view.queries.length) expect(view.queries).toHaveLength(15)
  }
  expect([...seen].sort()).toEqual(
    ['done', 'failed', 'provisional', 'queued', 'running', 'skipped'].sort()
  )
})

it('shows a mid-run snapshot as determinate progress', () => {
  const view = foldRunEvents(reportEventsRunningFixture(FIXTURE_NOW), 0)
  const counts = runCounts(view)
  expect(view.state).toBe('running')
  expect(runPhase(view)).toBe('analyses')
  expect(counts.finished).toBe(9)
  expect(counts.running).toBe(1)
  expect(activityLabel(view)).toBe('Analyzing top queries, 9 of 15')
  expect(progressPercent(view)).toBe(60)
})

it('never rewinds a finished row on a late queued event', () => {
  let view = foldRunEvents(reportEventsFixture(FIXTURE_NOW), 0)
  const first = view.queries[0]
  view = applyRunEvent(view, {
    type: 'analysis',
    key: first.key,
    state: 'queued',
    reason: null,
    result_line: null,
    duration_s: null,
  })
  expect(view.queries[0].state).toBe(first.state)
})

it('keeps the clock moving between events while running', () => {
  const view = applyRunEvent(
    emptyRunView(0),
    { type: 'run', state: 'running', started_at: 0, elapsed_s: 10 },
    1_000
  )
  expect(elapsedSeconds(view, 6_000)).toBe(15)
  const done = applyRunEvent(
    view,
    { type: 'run', state: 'done', started_at: 0, elapsed_s: 20 },
    7_000
  )
  expect(elapsedSeconds(done, 60_000)).toBe(20)
})

it('reads indeterminate while writing the report', () => {
  const events = reportEventsFixture(FIXTURE_NOW)
  const writing = events.findIndex(
    (e) => e.type === 'step' && e.step === 'narrative' && e.state === 'running'
  )
  const view = foldRunEvents(events.slice(0, writing + 1), 0)
  expect(runPhase(view)).toBe('narrative')
  expect(progressPercent(view)).toBeUndefined()
  expect(activityLabel(view)).toBe('Writing the report')
})
