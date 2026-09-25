import { describe, expect, it } from 'vitest'
import type { ChartMarker } from '@/lib/monitor/types'
import {
  clusterGap,
  clusterLead,
  clusterMarkers,
  markerTone,
  splitMarkers,
} from './markers'

const inbox = (ts: number, severity = 0.8): ChartMarker => ({
  ts,
  kind: 'inbox',
  label: `item ${ts}`,
  severity,
})

describe('splitMarkers', () => {
  it('sends index changes to the tick lane and drops causal verdicts', () => {
    const { events, index } = splitMarkers([
      {
        ts: 1,
        kind: 'index_added',
        label: 'idx added',
        matches_recommendation: true,
      },
      { ts: 2, kind: 'index_dropped', label: 'idx dropped' },
      { ts: 3, kind: 'fix_confirmed', label: 'Fix confirmed' },
      { ts: 4, kind: 'fix_no_effect', label: "Index didn't help" },
      inbox(5),
      { ts: 6, kind: 'report', label: 'Report written' },
    ])
    expect(index.map((m) => m.ts)).toEqual([1, 2])
    expect(events.map((m) => m.ts)).toEqual([5, 6])
    for (const marker of index) expect(markerTone(marker)).toBe('neutral')
  })
})

describe('clusterMarkers', () => {
  it('never leaves two clusters closer than the gap', () => {
    const markers = Array.from({ length: 60 }, (_, i) => inbox(i * 7 + (i % 3)))
    const gap = clusterGap(900)
    const clusters = clusterMarkers(markers, (ts) => ts * 2, gap)
    expect(clusters.reduce((sum, c) => sum + c.markers.length, 0)).toBe(60)
    for (let i = 1; i < clusters.length; i++)
      expect(clusters[i].x - clusters[i - 1].x).toBeGreaterThanOrEqual(gap)
    expect(clusters.length).toBeLessThanOrEqual(Math.ceil(840 / gap) + 1)
  })

  it('scales the gap with the plot but never below a glyph and a half', () => {
    expect(clusterGap(200)).toBe(28)
    expect(clusterGap(1400)).toBe(100)
  })

  it('opens the most severe, latest marker of a cluster', () => {
    const [cluster] = clusterMarkers(
      [inbox(1, 0.9), inbox(2, 0.5), inbox(3, 0.95)],
      (ts) => ts,
      28
    )
    expect(clusterLead(cluster).ts).toBe(3)
  })
})
