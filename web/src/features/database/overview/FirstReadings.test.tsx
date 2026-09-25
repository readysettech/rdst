import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FirstRunStrip } from './FirstReadings'
import { type FirstRunStep, firstRun } from './firstReadingsModel'

afterEach(cleanup)

it.each([
  'off',
  'done',
] as const)('keeps the completed strip honest when analysis is %s', (state) => {
  const model = firstRun({
    now: 1000,
    settings: {
      enabled: true,
      attention_every_min: 5,
      nightly_report: false,
      nightly_hour: 0,
      deep_analysis: false,
    },
    history: null,
    pulses: [],
    queue: null,
    queries: [{ key: 'a', analyzable: true, assessment: null }],
  })
  model.steps = model.steps.map(
    (step): FirstRunStep =>
      step.id === 'deep'
        ? {
            ...step,
            state,
            meta: state === 'done' ? '2 analyzed' : 'Turned off',
          }
        : step
  )
  render(<FirstRunStrip model={model} now={1000} />)
  expect(screen.getByTestId('first-run').textContent).toContain(
    state === 'off' ? 'Deep analysis is off.' : '2 analyzed.'
  )
  expect(screen.queryByText(/analyzed the busiest/)).toBeNull()
})
