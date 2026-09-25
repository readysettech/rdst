import { describe, expect, it } from 'vitest'
import { chartPauses, pauseAt } from './pauseModel'

const minute = (n: number) => 1_000_000 + n * 60

describe('chartPauses', () => {
  it('labels an outage and leaves other gaps without a cause', () => {
    const readings = [
      ...[0, 1, 2, 3].map(minute),
      ...[20, 21, 22].map(minute),
      ...[40, 41].map(minute),
    ]
    const pauses = chartPauses({
      readings,
      res: 60,
      domain: [minute(0), minute(45)],
      outages: [
        { start: minute(3), end: minute(20), cause: 'unreachable', item_id: 7 },
      ],
      now: minute(45),
    })
    expect(pauses).toEqual([
      {
        from: minute(3),
        to: minute(20),
        cause: 'unreachable',
        ongoing: false,
        itemId: 7,
      },
      { from: minute(23), to: minute(40), cause: 'unknown', ongoing: false },
    ])
    expect(pauseAt(pauses, minute(10))?.cause).toBe('unreachable')
    expect(pauseAt(pauses, minute(30))?.cause).toBe('unknown')
    expect(pauseAt(pauses, minute(1))).toBeNull()
  })

  it('runs an ongoing outage to now and clips to the domain', () => {
    const pauses = chartPauses({
      readings: [minute(0), minute(1)],
      res: 60,
      domain: [minute(0), minute(10)],
      outages: [{ start: minute(1), end: null, cause: 'unreachable', item_id: 1 }],
      now: minute(30),
    })
    expect(pauses).toEqual([
      {
        from: minute(1),
        to: minute(10),
        cause: 'unreachable',
        ongoing: true,
        itemId: 1,
      },
    ])
  })

  it('ignores gaps of three buckets or less', () => {
    expect(
      chartPauses({
        readings: [minute(0), minute(4)],
        res: 60,
        domain: [minute(0), minute(5)],
        now: minute(5),
      })
    ).toEqual([])
  })
})
