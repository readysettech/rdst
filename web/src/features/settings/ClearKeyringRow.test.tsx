import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { clearKeyring } from '../../lib/api'
import { ClearKeyringRow } from './ClearKeyringRow'

vi.mock('../../lib/api', () => ({
  clearKeyring: vi.fn(async () => ({ success: true })),
}))

afterEach(cleanup)

describe('ClearKeyringRow', () => {
  it('is one ghost row that clears the keyring only after the confirm', async () => {
    renderWithClient(<ClearKeyringRow />)
    const button = screen.getByRole('button', { name: 'Clear keyring' })
    expect(button.className).not.toContain('bg-surface-negative-solid')

    fireEvent.click(button)
    expect(screen.getByText('Clear the RDST keyring?')).toBeTruthy()
    expect(vi.mocked(clearKeyring)).not.toHaveBeenCalled()

    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: 'Clear keyring',
      })
    )
    await waitFor(() =>
      expect(vi.mocked(clearKeyring)).toHaveBeenCalledTimes(1)
    )
  })
})
