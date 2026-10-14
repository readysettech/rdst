import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getSettings,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { CA_NOW } from '@/lib/monitor/fixturesCa'
import { renderWithClient } from '@/test-utils'
import {
  NotificationSettings,
  quietHoursChoice,
  quietHoursFor,
} from './NotificationSettingsDialog'

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('ca-inbox')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  pushTo(true)
})

/** Push to this computer, as Settings stores it: the browser's choice and the OS permission. */
function pushTo(on: boolean) {
  vi.stubGlobal('Notification', {
    permission: 'granted',
    requestPermission: () => Promise.resolve('granted'),
  })
  localStorage.setItem('rdst_inbox_desktop_notifications', on ? '1' : '0')
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
  localStorage.clear()
})

async function openPopover() {
  renderWithClient(<NotificationSettings target="demo" />)
  fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
  return screen.findByRole('dialog', { name: 'Notifications' })
}

describe('NotificationSettings', () => {
  it('opens from the bell with three switches, one caption and the quiet hours', async () => {
    await openPopover()
    const actNow = await screen.findByLabelText('Act now items')
    await waitFor(() =>
      expect(actNow.getAttribute('aria-checked')).toBe('true')
    )
    expect(screen.getByLabelText('Fixes ready')).toBeTruthy()
    expect(
      screen.getByLabelText('Daily report').getAttribute('aria-checked')
    ).toBe('true')
    expect(
      screen.getByText(
        'Pushed once as a desktop notification. Nothing is sent by email.'
      )
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Quiet hours: 11 PM to 7 AM' })
    ).toBeTruthy()
  })

  it('reads Push to this computer as Settings does: off, no switch reads on', async () => {
    pushTo(false)
    await openPopover()
    const push = screen.getByLabelText('Push to this computer')
    expect(push.getAttribute('aria-checked')).toBe('false')
    for (const label of ['Act now items', 'Fixes ready', 'Daily report']) {
      const row = screen.getByLabelText(label)
      expect(row.getAttribute('aria-checked')).toBe('false')
      expect(row.hasAttribute('disabled')).toBe(true)
    }
    expect(
      screen.getByText(
        'Push to this computer is off, so nothing is pushed. Nothing is sent by email.'
      )
    ).toBeTruthy()

    fireEvent.click(push)
    await waitFor(() =>
      expect(
        screen.getByLabelText('Act now items').getAttribute('aria-checked')
      ).toBe('true')
    )
    expect(localStorage.getItem('rdst_inbox_desktop_notifications')).toBe('1')
  })

  it('sends a test notification while push is on', async () => {
    const shown = vi.fn()
    vi.stubGlobal(
      'Notification',
      Object.assign(shown, {
        permission: 'granted',
        requestPermission: () => Promise.resolve('granted'),
      })
    )
    await openPopover()
    fireEvent.click(
      screen.getByRole('button', { name: 'Send a test notification' })
    )
    expect(shown).toHaveBeenCalledWith('RDST test notification', {
      body: undefined,
      tag: undefined,
    })
  })

  it('says the switches come with a database when there is none', async () => {
    renderWithClient(<NotificationSettings />)
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    await screen.findByRole('dialog', { name: 'Notifications' })
    expect(
      screen.getByText(
        'Add a database to choose what it pushes. Nothing is sent by email.'
      )
    ).toBeTruthy()
    expect(screen.queryByLabelText('Act now items')).toBeNull()
  })

  it('writes a switch to the database settings', async () => {
    await openPopover()
    const fixes = await screen.findByLabelText('Fixes ready')
    await waitFor(() => expect(fixes.getAttribute('aria-checked')).toBe('true'))
    fireEvent.click(fixes)
    await waitFor(async () =>
      expect((await getSettings('demo')).notify_events?.query_changes).toBe(
        false
      )
    )
    expect((await getSettings('demo')).notify_events?.stuck_query).toBe(true)
  })

  it('writes the daily report switch to the database settings', async () => {
    await openPopover()
    const daily = await screen.findByLabelText('Daily report')
    await waitFor(() => expect(daily.getAttribute('aria-checked')).toBe('true'))
    fireEvent.click(daily)
    await waitFor(async () =>
      expect((await getSettings('demo')).notify_events?.daily_report).toBe(
        false
      )
    )
    expect((await getSettings('demo')).quiet_hours).toEqual({
      from_hour: 23,
      to_hour: 7,
    })
  })

  it('opens the mute rules, each with Unmute, with or without a database', async () => {
    // The seeded expiring rule is timed against the fixture clock.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(CA_NOW * 1000)
    renderWithClient(<NotificationSettings />)
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    fireEvent.click(
      await screen.findByRole('button', { name: 'Show mute rules' })
    )
    const dialog = await screen.findByRole('dialog', { name: 'Mute rules' })
    await waitFor(() =>
      expect(
        within(dialog).getAllByRole('button', { name: 'Unmute' }).length
      ).toBe(3)
    )
    expect(screen.queryByRole('dialog', { name: 'Notifications' })).toBeNull()
  })

  it('reads the quiet hours from the settings and maps the menu to hours', () => {
    expect(quietHoursChoice(undefined)).toBe('11 PM to 7 AM')
    expect(quietHoursChoice({ from_hour: 23, to_hour: 7 })).toBe(
      '11 PM to 7 AM'
    )
    expect(quietHoursChoice({ from_hour: 0, to_hour: 0 })).toBe('Off')
    expect(quietHoursFor('Off')).toEqual({ from_hour: 0, to_hour: 0 })
    expect(quietHoursFor('11 PM to 7 AM')).toEqual({
      from_hour: 23,
      to_hour: 7,
    })
  })
})
