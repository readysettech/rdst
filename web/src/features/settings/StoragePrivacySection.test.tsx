import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StoragePrivacySection } from './StoragePrivacySection'

describe('StoragePrivacySection', () => {
  afterEach(cleanup)

  it('requires an explicit confirmation before removing local data', async () => {
    const onReset = vi.fn()
    renderSection({ onReset })

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove all local data' })
    )

    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByRole('heading', {
        name: 'Remove all local RDST data?',
      })
    ).toBeTruthy()
    expect(within(dialog).getByText('/tmp/rdst-test')).toBeTruthy()
    expect(
      within(dialog).getByText(/Your ~\/\.ssh directory is unchanged/)
    ).toBeTruthy()
    expect(onReset).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', {
          name: 'Remove all local RDST data?',
        })
      ).toBeNull()
    )
    expect(onReset).not.toHaveBeenCalled()
  })

  it('runs the destructive action once from the confirmation dialog', async () => {
    const onReset = vi.fn()
    renderSection({ onReset })

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove all local data' })
    )
    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove local data' })
    )

    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('keeps reset errors inside the confirmation context', async () => {
    renderSection({ resetError: 'The local directory is locked.' })

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove all local data' })
    )

    expect(
      await screen.findByText('The local directory is locked.')
    ).toBeTruthy()
  })
})

function renderSection(
  overrides: Partial<Parameters<typeof StoragePrivacySection>[0]> = {}
) {
  return render(
    <StoragePrivacySection
      dataDirectory="/tmp/rdst-test"
      resetPending={false}
      resetError={null}
      onReset={vi.fn()}
      {...overrides}
    />
  )
}
