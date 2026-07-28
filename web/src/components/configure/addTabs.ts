/**
 * The Add Targets drawer's source tabs, shared with the routes that deep-link
 * into it (`/configure?add=` and the retired `/fleet?add=`).
 */

export const ADD_TABS = [
  'aws',
  'supabase',
  'neon',
  'digitalocean',
  'csv',
] as const

export type AddTab = (typeof ADD_TABS)[number]

/** Parse-only: an unknown tab reads as "no deep link", never as a throw. */
export function parseAddTab(value: unknown): AddTab | undefined {
  return ADD_TABS.includes(value as AddTab) ? (value as AddTab) : undefined
}
