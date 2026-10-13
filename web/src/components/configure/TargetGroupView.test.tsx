import { describe, expect, it } from 'vitest'
import { groupTargets } from './TargetGroupView'

interface Row {
  name: string
  group: string | null
}

const rows: Row[] = [
  { name: 'aurora-writer', group: 'production' },
  { name: 'aurora-reader', group: 'production' },
  { name: 'old-dead', group: null },
]

describe('groupTargets', () => {
  it('keeps named groups in first-seen order and puts the remainder last', () => {
    expect(groupTargets(rows, (row) => row.group)).toEqual([
      { group: 'production', targets: [rows[0], rows[1]] },
      { group: null, targets: [rows[2]] },
    ])
  })

  it('omits the ungrouped block when everything carries a group', () => {
    expect(
      groupTargets([rows[0], rows[1]], (row) => row.group).map(
        (block) => block.group
      )
    ).toEqual(['production'])
  })
})
