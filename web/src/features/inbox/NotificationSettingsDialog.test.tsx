import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DesktopNotificationsBanner } from '@/components/DesktopNotificationsBanner'
import type { DesktopNotificationHealth } from '@/lib/desktop'
import {
  getSettings,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { renderWithClient } from '@/test-utils'
import { NotificationSettingsDialog } from './NotificationSettingsDialog'

function renderDialog() {
  return renderWithClient(
    <NotificationSettingsDialog
      open
      onClose={() => {}}
      target="demo"
      rules={[]}
      loading={false}
      names={new Map()}
    />
  )
}

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
  delete window.rdstDesktop
})

describe('NotificationSettingsDialog', () => {
  it.each([
    'linux_no_daemon',
    'toast_failed',
  ])('shares notification health with the banner for %s', async (reason) => {
    const health: DesktopNotificationHealth = {
      blocked: true,
      reason,
      message: "System notifications aren't available.",
      trayLabel: null,
      settingsUrl: null,
      checkedAt: 1,
    }
    const listeners: ((health: DesktopNotificationHealth) => void)[] = []
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
      notifications: {
        getHealth: async () => health,
        onHealthChange: (listener) => {
          listeners.push(listener)
          return () => {}
        },
        openSettings: vi.fn(),
      },
    }
    renderDialog()
    renderWithClient(<DesktopNotificationsBanner />)
    const channel = screen.getByText('System notifications.').closest('li')!
    await waitFor(() =>
      expect(within(channel).getByText('Not available')).toBeTruthy()
    )
    expect(
      await screen.findByTestId('desktop-notifications-banner')
    ).toBeTruthy()
    act(() => {
      for (const listener of listeners)
        listener({ ...health, blocked: false, reason: null, message: null })
    })
    expect(within(channel).getByText('Available')).toBeTruthy()
    expect(screen.queryByTestId('desktop-notifications-banner')).toBeNull()
  })

  it.each([
    'denied',
    'unsupported',
    'granted',
    'default',
  ])('uses browser support and permission %s', (permission) => {
    vi.stubGlobal(
      'Notification',
      permission === 'unsupported' ? undefined : { permission }
    )
    if (permission === 'unsupported')
      Reflect.deleteProperty(window, 'Notification')
    renderDialog()
    const channel = screen.getByText('This browser.').closest('li')!
    expect(
      within(channel).getByText(
        ['granted', 'default'].includes(permission)
          ? 'Available'
          : 'Not available'
      )
    ).toBeTruthy()
    expect(
      within(
        screen.getByText('System notifications.').closest('li')!
      ).getByText('Not available')
    ).toBeTruthy()
  })

  it('in a browser, points to the desktop app and keeps the browser switch', async () => {
    renderDialog()
    expect(
      await screen.findByText(/desktop app shows system notifications/)
    ).toBeTruthy()
    expect(screen.getByLabelText('Browser notifications')).toBeTruthy()
    expect(screen.getByText(/Email and phone/)).toBeTruthy()
  })

  it('in the desktop app, system notifications are the switch and browser ones are hidden', async () => {
    const set = vi.fn(async (enabled: boolean) => enabled)
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'win32',
      getNotificationsEnabled: async () => true,
      setNotificationsEnabled: set,
    }
    renderDialog()
    const system = await screen.findByLabelText('System notifications')
    await waitFor(() =>
      expect(system.getAttribute('aria-checked')).toBe('true')
    )
    expect(screen.queryByLabelText('Browser notifications')).toBeNull()
    fireEvent.click(system)
    await waitFor(() => expect(set).toHaveBeenCalledWith(false))
  })

  it('lists what notifies, with database unreachable on by default', async () => {
    renderDialog()
    const unreachable = await screen.findByLabelText('Database unreachable')
    await waitFor(() =>
      expect(unreachable.getAttribute('aria-checked')).toBe('true')
    )
    const resumed = screen.getByLabelText('Monitoring resumed')
    expect(resumed.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(resumed)
    await waitFor(async () =>
      expect(
        (await getSettings('demo')).notify_events?.database_recovered
      ).toBe(true)
    )
    expect(
      (await getSettings('demo')).notify_events?.database_unreachable
    ).toBe(true)
  })
})
