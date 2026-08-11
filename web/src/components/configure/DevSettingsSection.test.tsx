import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { clearKeyring, simulateTrialExhausted } from '../../lib/api'
import { useTrialSource } from '../../lib/trialQueries'
import { DevSettingsSection } from './DevSettingsSection'

vi.mock('../../lib/api', () => ({
  clearKeyring: vi.fn(),
  simulateTrialExhausted: vi.fn(),
}))

vi.mock('../../lib/trialQueries', () => ({
  invalidateTrialRelatedQueries: vi.fn(),
  useTrialSource: vi.fn(),
}))

describe('DevSettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: undefined,
      anthropicRequirement: undefined,
      anthropicSource: undefined,
      isTrialSource: false,
      trialStatus: undefined,
    })
  })

  afterEach(cleanup)

  it('does not clear the keyring without explicit confirmation', async () => {
    renderWithClient(<DevSettingsSection />)

    fireEvent.click(screen.getByRole('button', { name: 'Clear keyring' }))

    expect(
      await screen.findByRole('heading', { name: 'Clear the RDST keyring?' })
    ).toBeTruthy()
    expect(clearKeyring).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: 'Clear the RDST keyring?' })
      ).toBeNull()
    )
    expect(clearKeyring).not.toHaveBeenCalled()
  })

  it('keeps a clear-keyring failure in the confirmation dialog', async () => {
    vi.mocked(clearKeyring).mockRejectedValue(
      new Error('The system keyring is unavailable.')
    )
    renderWithClient(<DevSettingsSection />)

    fireEvent.click(screen.getByRole('button', { name: 'Clear keyring' }))
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Clear keyring',
      })
    )

    expect(
      await screen.findByText('The system keyring is unavailable.')
    ).toBeTruthy()
    expect(clearKeyring).toHaveBeenCalledTimes(1)
  })

  it('shows the trial simulator only for an active trial source', async () => {
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: undefined,
      anthropicRequirement: undefined,
      anthropicSource: 'trial',
      isTrialSource: true,
      trialStatus: { status: 'active', active: true },
    })
    vi.mocked(simulateTrialExhausted).mockResolvedValue({
      success: true,
      message: 'Trial has been marked as exhausted.',
    })
    renderWithClient(<DevSettingsSection />)

    fireEvent.click(screen.getByRole('button', { name: 'Simulate exhausted' }))

    expect(
      await screen.findByText('Trial has been marked as exhausted.')
    ).toBeTruthy()
    expect(simulateTrialExhausted).toHaveBeenCalledTimes(1)
  })
})
