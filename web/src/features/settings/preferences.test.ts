import { afterEach, expect, it, vi } from 'vitest'
import {
  FONT_SIZE_LABEL,
  getPreferences,
  KEEP_READINGS,
  keepReadingsLabel,
  PREFERENCE_DEFAULTS,
  putPreferences,
  THEME_LABEL,
} from './preferences'

afterEach(() => vi.unstubAllGlobals())

it('names every choice', () => {
  expect(Object.values(THEME_LABEL)).toEqual(['Dark', 'Light', 'System'])
  expect(Object.values(FONT_SIZE_LABEL)).toEqual(['Default', 'Large'])
  expect(KEEP_READINGS.map(keepReadingsLabel)).toEqual([
    '7 days',
    '30 days',
    '90 days',
  ])
  expect(PREFERENCE_DEFAULTS).toEqual({
    theme: 'dark',
    font_size: 'default',
    keep_readings_days: 7,
  })
})

it('reads and writes the preferences store', async () => {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
    Response.json({
      ...PREFERENCE_DEFAULTS,
      ...(init?.body ? JSON.parse(String(init.body)) : {}),
    })
  )
  vi.stubGlobal('fetch', fetchMock)
  expect(await getPreferences()).toEqual(PREFERENCE_DEFAULTS)
  expect(fetchMock).toHaveBeenCalledWith('/api/settings/preferences')
  expect(
    await putPreferences({ theme: 'light', keep_readings_days: 30 })
  ).toEqual({
    theme: 'light',
    font_size: 'default',
    keep_readings_days: 30,
  })
  const [, init] = fetchMock.mock.calls[1]
  expect(init?.method).toBe('PUT')
  expect(JSON.parse(String(init?.body))).toEqual({
    theme: 'light',
    keep_readings_days: 30,
  })
})

it('surfaces a failed write', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('bad choice', { status: 422 }))
  )
  await expect(putPreferences({ theme: 'light' })).rejects.toThrow('bad choice')
})
