import { describe, expect, it } from 'vitest'
import { OTHER_KEY } from './loadModel'
import {
  buildOverlay,
  OVERLAY_COLOR,
  overlayValues,
  pickHighlight,
} from './overlayModel'

const band = (key: string, isOther = false) => ({
  key,
  label: key,
  color: 'var(--mk-band-1)',
  share: 0.1,
  totalMs: 1,
  calls: 1,
  meanMs: 1,
  isOther,
})
const model = { bands: [band('a'), band('b'), band(OTHER_KEY, true)] }
const local = new Set(['a', 'b', 'p3', 'hovered'])
const hasSeries = (key: string) => local.has(key)

describe('pickHighlight', () => {
  it('raises the open query when it has a band', () => {
    expect(
      pickHighlight(model, { openKey: 'a', hoverKey: null, hasSeries })
    ).toEqual({ key: 'a', mode: 'band' })
  })

  it('draws a line for an open query without a band (page 3, Everything else)', () => {
    expect(
      pickHighlight(model, { openKey: 'p3', hoverKey: null, hasSeries })
    ).toEqual({ key: 'p3', mode: 'overlay' })
    // Not even in the list: it's fetched, so it still gets a line.
    expect(
      pickHighlight(model, { openKey: 'far', hoverKey: null, hasSeries })
    ).toEqual({ key: 'far', mode: 'overlay' })
  })

  it('shows a hovered row without a band while the pointer is on it', () => {
    expect(
      pickHighlight(model, { openKey: 'a', hoverKey: 'hovered', hasSeries })
    ).toEqual({ key: 'hovered', mode: 'overlay' })
  })

  it("leaves hovered bands to the chart's own raise, and never fetches for a hover", () => {
    expect(
      pickHighlight(model, { openKey: 'p3', hoverKey: 'b', hasSeries })
    ).toEqual({ key: 'p3', mode: 'overlay' })
    expect(
      pickHighlight(model, { openKey: 'p3', hoverKey: OTHER_KEY, hasSeries })
    ).toEqual({ key: 'p3', mode: 'overlay' })
    expect(
      pickHighlight(model, { openKey: null, hoverKey: 'unlisted', hasSeries })
    ).toBeNull()
  })

  it('shows nothing with no open or hovered query', () => {
    expect(
      pickHighlight(model, { openKey: null, hoverKey: null, hasSeries })
    ).toBeNull()
    expect(
      pickHighlight(null, { openKey: 'a', hoverKey: null, hasSeries })
    ).toEqual({ key: 'a', mode: 'overlay' })
  })
})

describe('overlayValues', () => {
  // Minute bands; the query's own series in 5-minute buckets.
  const xs = Array.from({ length: 10 }, (_, i) => 1000 + i * 60)
  const loadModel = {
    xs,
    res: 60,
    // Two bands, 100 and 300 ms/s: 400 ms of database time per second.
    load: [xs.map(() => 100), xs.map(() => 300)],
  }
  const points = [
    { ts: 1000, qps: 2, mean_ms: 10 },
    { ts: 1300, qps: 4, mean_ms: null },
    { ts: 1600, qps: 1, mean_ms: 5 },
  ]
  const NOW = 5000

  it('reads calls per second and latency straight from the series', () => {
    expect(overlayValues(loadModel, 'calls', points, NOW)).toEqual({
      xs: [1000, 1300, 1600],
      values: [2, 4, 1],
    })
    expect(overlayValues(loadModel, 'latency', points, NOW).values).toEqual([
      10,
      null,
      5,
    ])
  })

  it("gives time as the query's share of the same bucket's database time", () => {
    const { values } = overlayValues(loadModel, 'time', points, NOW)
    // 2 calls/s x 10 ms = 20 ms/s of 400: 5%.
    expect(values[0]).toBeCloseTo(5)
    expect(values[1]).toBeNull()
    // The last bucket has no minute reading inside it; the nearest one stands in.
    expect(values[2]).toBeCloseTo(1.25)
  })

  it('leaves out the bucket still filling up', () => {
    const { xs: kept } = overlayValues(loadModel, 'calls', points, 1650)
    expect(kept).toEqual([1000, 1300])
  })

  it('builds a labelled overlay in the ink color, or nothing', () => {
    const overlay = buildOverlay(
      loadModel,
      'calls',
      { key: 'p3', label: 'COUNT on payments', series: points },
      NOW
    )
    expect(overlay).toMatchObject({
      id: 'p3',
      label: 'COUNT on payments',
      color: OVERLAY_COLOR,
      values: [2, 4, 1],
    })
    expect(buildOverlay(loadModel, 'calls', null, NOW)).toBeNull()
    expect(
      buildOverlay(null, 'calls', { key: 'p3', label: 'x', series: [] }, NOW)
    ).toBeNull()
  })
})
