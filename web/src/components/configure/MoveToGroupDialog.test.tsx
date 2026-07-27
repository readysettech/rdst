import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { updateFleetTargetGroup } from '../../lib/useFleet'
import { MoveToGroupDialog } from './MoveToGroupDialog'

vi.mock('../../lib/useFleet', () => ({
  updateFleetTargetGroup: vi.fn(),
}))

function renderDialog(
  target: { name: string; group?: string | null } | null,
  onClose = vi.fn()
) {
  const result = renderWithClient(
    <MoveToGroupDialog
      target={target}
      groups={['production', 'archive']}
      onClose={onClose}
    />
  )
  return { ...result, onClose }
}

describe('MoveToGroupDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(updateFleetTargetGroup).mockResolvedValue(undefined)
  })

  afterEach(cleanup)

  it('creates a new group from the free-text field', async () => {
    const { onClose } = renderDialog({ name: 'old-dead', group: null })

    expect(screen.getByText('Move old-dead to group')).toBeTruthy()

    // The modal renders in a portal, so it is not under the render container.
    const input = document.querySelector(
      'input[name="move-target-new-group"]'
    ) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'archive' } })
    fireEvent.click(screen.getByRole('button', { name: 'Move target' }))

    await waitFor(() =>
      expect(updateFleetTargetGroup).toHaveBeenCalledWith('old-dead', 'archive')
    )
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('clears the group when removing a grouped target', async () => {
    renderDialog({ name: 'aurora-writer', group: 'production' })

    fireEvent.click(screen.getByRole('button', { name: 'Remove from group' }))

    await waitFor(() =>
      expect(updateFleetTargetGroup).toHaveBeenCalledWith('aurora-writer', null)
    )
  })

  it('keeps Remove from group unavailable for an ungrouped target', () => {
    renderDialog({ name: 'old-dead', group: null })

    expect(
      screen.getByRole('button', { name: 'Remove from group' })
    ).toHaveProperty('disabled', true)
  })

  it('reports a failed move instead of closing', async () => {
    vi.mocked(updateFleetTargetGroup).mockRejectedValue(
      new Error('Target not found')
    )
    const { onClose } = renderDialog({ name: 'old-dead', group: null })

    const input = document.querySelector(
      'input[name="move-target-new-group"]'
    ) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'archive' } })
    fireEvent.click(screen.getByRole('button', { name: 'Move target' }))

    await waitFor(() =>
      expect(screen.getByText('Target not found')).toBeTruthy()
    )
    expect(onClose).not.toHaveBeenCalled()
  })
})
