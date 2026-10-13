// App preferences (GET/PUT /api/settings/preferences): theme, font size and how long
// readings stay. Kept in the RDST config file by the backend; the menus on the Settings
// page read and write them.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

export type Theme = 'dark' | 'light' | 'system'
export type FontSize = 'default' | 'large'
export type KeepReadingsDays = 7 | 30 | 90

export interface Preferences {
  theme: Theme
  font_size: FontSize
  keep_readings_days: KeepReadingsDays
}

export const PREFERENCE_DEFAULTS: Preferences = {
  theme: 'dark',
  font_size: 'default',
  keep_readings_days: 7,
}

export const THEMES: readonly Theme[] = ['dark', 'light', 'system']
export const FONT_SIZES: readonly FontSize[] = ['default', 'large']
export const KEEP_READINGS: readonly KeepReadingsDays[] = [7, 30, 90]

export const THEME_LABEL: Record<Theme, string> = {
  dark: 'Dark',
  light: 'Light',
  system: 'System',
}
export const FONT_SIZE_LABEL: Record<FontSize, string> = {
  default: 'Default',
  large: 'Large',
}

export function keepReadingsLabel(days: KeepReadingsDays): string {
  return `${days} days`
}

const URL = '/api/settings/preferences'

async function json<T>(response: Response): Promise<T> {
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText)
  return response.json() as Promise<T>
}

export const getPreferences = () =>
  fetch(URL).then((response) => json<Preferences>(response))

export const putPreferences = (update: Partial<Preferences>) =>
  fetch(URL, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  }).then((response) => json<Preferences>(response))

export const preferencesKey = ['settings', 'preferences'] as const

export function usePreferences() {
  return useQuery({
    queryKey: preferencesKey,
    queryFn: getPreferences,
    staleTime: 60_000,
  })
}

export function useUpdatePreferences() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: putPreferences,
    onSuccess: (preferences) =>
      client.setQueryData(preferencesKey, preferences),
  })
}
