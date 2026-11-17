import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderWithClient } from '@/test-utils'
import type { DesktopLoginStatus } from '../../lib/desktop'
import { StartAtLoginGroup } from './StartAtLoginGroup'

function desktop(status: DesktopLoginStatus) {
  const setStartAtLogin = vi.fn(async (enabled: boolean) => ({
    enabled,
    available: true,
    ask: false,
  }))
  window.rdstDesktop = {
    isDesktop: true,
    platform: 'win32',
    getStartAtLogin: vi.fn(async () => status),
    setStartAtLogin,
  }
  return setStartAtLogin
}

describe('StartAtLoginGroup', () => {
  afterEach(() => {
    cleanup()
    delete window.rdstDesktop
  })

  it('switches Start at login', async () => {
    const set = desktop({ enabled: false, available: true, ask: true })
    renderWithClient(<StartAtLoginGroup />)

    fireEvent.click(
      await screen.findByRole('switch', { name: 'Start at login' })
    )
    await waitFor(() => expect(set).toHaveBeenCalledWith(true))
    await waitFor(() =>
      expect(
        screen
          .getByRole('switch', { name: 'Start at login' })
          .getAttribute('aria-checked')
      ).toBe('true')
    )
  })

  it('explains and disables the switch where it is unavailable', async () => {
    desktop({
      enabled: false,
      available: false,
      note: 'Start at login is unavailable in AppImage builds.',
    })
    renderWithClient(<StartAtLoginGroup />)

    await screen.findByText('Start at login is unavailable in AppImage builds.')
    expect(
      screen
        .getByRole('switch', { name: 'Start at login' })
        .hasAttribute('disabled')
    ).toBe(true)
  })

  it('shows nothing in a browser', () => {
    const { container } = renderWithClient(<StartAtLoginGroup />)
    expect(container.textContent).toBe('')
  })
})
