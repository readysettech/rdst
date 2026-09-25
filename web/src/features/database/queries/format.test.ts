import { expect, it } from 'vitest'
import { analysisValuesLine, comparedLabel } from './format'

it('prefers the stored compare outcome wording', () => {
  expect(
    comparedLabel({
      ts: 1,
      verdict: 'faster',
      speedup: 4,
      speedup_label: '4.0x faster',
    })
  ).toBe('Compared: 4.0x faster')
  expect(comparedLabel({ ts: 1, verdict: 'slower', speedup: 0.8 })).toBe(
    'Compared: 0.8x slower'
  )
  expect(comparedLabel(null)).toBeNull()
})

it('says which values an analysis used', () => {
  expect(analysisValuesLine({ mode: 'analyze', param_source: 'sampled' })).toBe(
    'Measured with values from live traffic'
  )
  expect(
    analysisValuesLine({ mode: 'analyze', param_source: 'table_sampled' })
  ).toBe(
    "Measured with values taken from the table's own data, not from live traffic"
  )
  expect(analysisValuesLine({ mode: 'explain', param_source: 'none' })).toBe(
    "Plan only: no real values were available, so RDST didn't run the query"
  )
  // Plan only by choice, with values at hand, or a query without parameters.
  expect(
    analysisValuesLine({ mode: 'explain', param_source: 'sampled' })
  ).toBeNull()
  expect(
    analysisValuesLine({ mode: 'analyze', param_source: 'none' })
  ).toBeNull()
  expect(analysisValuesLine(null)).toBeNull()
})
