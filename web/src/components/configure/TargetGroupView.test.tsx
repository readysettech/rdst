import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { groupTargets, TargetGroupView } from './TargetGroupView'

interface Row {
  name: string
  group: string | null
}

const rows: Row[] = [
  { name: 'aurora-writer', group: 'production' },
  { name: 'aurora-reader', group: 'production' },
  { name: 'old-dead', group: null },
]

const renderRows = (targets: Row[]) => (
  <ul>
    {targets.map((target) => (
      <li key={target.name}>{target.name}</li>
    ))}
  </ul>
)

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

describe('TargetGroupView', () => {
  afterEach(cleanup)

  it('nests the rows under an always-visible header per group', () => {
    render(
      <TargetGroupView
        targets={rows}
        groupOf={(row) => row.group}
        renderTargets={renderRows}
      />
    )

    expect(screen.getByText('production')).toBeTruthy()
    expect(screen.getByText('2 targets')).toBeTruthy()
    expect(screen.getByText('Ungrouped')).toBeTruthy()
    expect(screen.getByText('1 target')).toBeTruthy()
    // Every block's rows are rendered, with nothing to expand first.
    expect(screen.getByText('aurora-writer')).toBeTruthy()
    expect(screen.getByText('aurora-reader')).toBeTruthy()
    expect(screen.getByText('old-dead')).toBeTruthy()
  })

  it('renders no group headers when nothing carries a real group', () => {
    render(
      <TargetGroupView
        targets={[{ name: 'solo', group: null }]}
        groupOf={(row) => row.group}
        renderTargets={renderRows}
      />
    )

    expect(screen.queryByText('Ungrouped')).toBeNull()
    expect(screen.getByText('solo')).toBeTruthy()
  })

  it('offers the group-scoped health check only for real groups', () => {
    const onHealthCheckGroup = vi.fn()
    render(
      <TargetGroupView
        targets={rows}
        groupOf={(row) => row.group}
        renderTargets={renderRows}
        onHealthCheckGroup={onHealthCheckGroup}
      />
    )

    const buttons = screen.getAllByRole('button', {
      name: /Health check this group/,
    })
    expect(buttons).toHaveLength(1)
    fireEvent.click(buttons[0])
    expect(onHealthCheckGroup).toHaveBeenCalledWith('production')
  })
})
