import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { ShutdownDialog } from './ShutdownDialog'

afterEach(cleanup)

describe('ShutdownDialog', () => {
  it('names what goes and what stays, with a red confirm and a ghost escape', () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn()
    renderWithClient(
      <ShutdownDialog isOpen onClose={onClose} onConfirm={onConfirm} />
    )
    const dialog = screen.getByRole('dialog', { name: 'Shut down the demo?' })
    expect(dialog.textContent).toContain(
      "Removes the demo's four containers and the orders data. Nothing else on this computer changes; the images stay for next time."
    )
    expect(dialog.textContent).toContain('About 10 seconds.')
    const confirm = screen.getByRole('button', { name: 'Shut down' })
    expect(confirm.className).toContain('bg-surface-negative-solid')
    // The close mark is the only glyph: no icon tile, no notice box.
    expect(dialog.querySelectorAll('svg').length).toBeLessThanOrEqual(1)
    fireEvent.click(screen.getByRole('button', { name: 'Keep running' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('renders nothing while closed', () => {
    renderWithClient(
      <ShutdownDialog isOpen={false} onClose={vi.fn()} onConfirm={vi.fn()} />
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
