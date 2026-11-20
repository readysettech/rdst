import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetStubState, setFixtureTiming } from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { renderWithClient } from '@/test-utils'
import { NotificationSettings } from './NotificationSettingsDialog'

const WEBHOOK = 'https://hooks.slack.com/services/T000/B000/xyz'

let slack: Record<string, unknown>
let calls: { method: string; url: string; body?: unknown }[]

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
  // Push to this computer off, as in a server with no browser permission.
  vi.stubGlobal('Notification', {
    permission: 'default',
    requestPermission: () => Promise.resolve('default'),
  })
  localStorage.setItem('rdst_inbox_desktop_notifications', '0')
  slack = { configured: false, source: null }
  calls = []
  const realFetch = globalThis.fetch
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (!url.startsWith('/api/settings/slack')) return realFetch(input, init)
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, url, body })
      if (url.endsWith('/test')) return Response.json({ ok: true, error: null })
      if (method === 'PUT') slack = { configured: true, source: 'settings' }
      if (method === 'DELETE') slack = { configured: false, source: null }
      return Response.json(slack)
    })
  )
})

afterEach(() => {
  cleanup()
  setFixtureOverride(undefined)
  vi.unstubAllGlobals()
  localStorage.clear()
})

async function openPopover() {
  renderWithClient(<NotificationSettings target="demo" />)
  fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
  return screen.findByRole('dialog', { name: 'Notifications' })
}

describe('Slack notifications', () => {
  it('saves a webhook, tests it and turns the push switches on', async () => {
    await openPopover()
    const input = await screen.findByLabelText('Slack webhook URL')
    expect(input.getAttribute('type')).toBe('password')
    expect(
      screen.getByLabelText('Act now items').hasAttribute('disabled')
    ).toBe(true)

    fireEvent.change(input, { target: { value: WEBHOOK } })
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Send a test message to this webhook',
      })
    )
    expect((await screen.findByRole('status')).textContent).toBe(
      'Sent. Check the Slack channel or DM.'
    )
    expect(calls.at(-1)).toEqual({
      method: 'POST',
      url: '/api/settings/slack/test',
      body: { webhook_url: WEBHOOK },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText('Pushes go to your Slack webhook.')
    ).toBeTruthy()
    expect(calls).toContainEqual({
      method: 'PUT',
      url: '/api/settings/slack',
      body: { webhook_url: WEBHOOK },
    })
    await waitFor(() =>
      expect(
        screen.getByLabelText('Act now items').hasAttribute('disabled')
      ).toBe(false)
    )
    expect(
      screen.getByText('Pushed once to Slack. Nothing is sent by email.')
    ).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: 'Remove the Slack webhook' })
    )
    expect(await screen.findByLabelText('Slack webhook URL')).toBeTruthy()
  })

  it('shows an environment webhook without a Remove button, and the last failure', async () => {
    slack = {
      configured: true,
      source: 'env',
      last_error: 'Slack answered 404: no_service.',
    }
    await openPopover()
    expect(
      await screen.findByText(
        'Pushes go to the Slack webhook in RDST_SLACK_WEBHOOK_URL.'
      )
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Remove the Slack webhook' })
    ).toBeNull()
    expect(screen.getByRole('status').textContent).toBe(
      'Last message failed: Slack answered 404: no_service.'
    )
  })
})
