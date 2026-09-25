import { describe, expect, it } from 'vitest'
import { latencyStatus } from './LatencyStatusLine'

const points = (ms: number[]) =>
  ms.map((mean_ms, index) => ({
    ts: 1_790_000_000 + index * 60,
    qps: 5,
    mean_ms,
  }))

describe('latencyStatus', () => {
  it('reports a recovery from its own readings, naming no cause', () => {
    const status = latencyStatus(points([4, 4, 4, 315, 320, 310, 4, 4, 4, 4]))
    expect(status?.kind).toBe('recovered')
    expect(status?.title).toBe('Latency back to normal')
    expect(status?.sentence).toMatch(
      /^Back to normal since .+ \(320 -> 4\.0 ms\)\.$/
    )
    expect(status?.sentence).not.toMatch(/idx|index/i)
  })

  it('flags a query that is still slower than its baseline', () => {
    const status = latencyStatus(points([4, 4, 4, 4, 4, 4, 4, 30, 32, 31]))
    expect(status?.kind).toBe('slower')
    expect(status?.title).toBe('Still slower than its baseline')
    expect(status?.sentence).toBe(
      '31 ms now against a typical 4.0 ms over this range.'
    )
  })

  it('says nothing for a steady query or too few readings', () => {
    expect(latencyStatus(points([4, 4.2, 3.9, 4.1, 4, 4, 4]))).toBeNull()
    expect(latencyStatus(points([4, 300, 4]))).toBeNull()
  })

  it('ignores sub-0.1 ms jitter on very fast queries', () => {
    expect(
      latencyStatus(points([0.02, 0.02, 0.02, 0.09, 0.02, 0.02, 0.02, 0.02]))
    ).toBeNull()
  })
})
