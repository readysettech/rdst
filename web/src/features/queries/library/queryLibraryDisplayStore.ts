import {
  QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES,
  QUERY_LIBRARY_DISPLAY_MODES,
  QUERY_LIBRARY_DISPLAY_PROPERTIES,
  type QueryLibraryDisplayMode,
  type QueryLibraryDisplayProperty,
} from './queryLibraryDisplay'

const DISPLAY_STORAGE_KEY = 'rdst-queries-display'

/**
 * The Queries page opens on Card 2 by default (RDST UX follow-up): denser
 * evidence up front, with Card 1 and List staying available as alternatives.
 */
export const QUERY_LIBRARY_DEFAULT_DISPLAY_MODE: QueryLibraryDisplayMode =
  'card-2'

/**
 * The rows/list mode stays wired end to end but is hidden from the Display
 * menu for now, so it neither offers nor persists as a user choice.
 */
export const QUERY_LIBRARY_VISIBLE_DISPLAY_MODES =
  QUERY_LIBRARY_DISPLAY_MODES.filter((option) => option.value !== 'rows')

export type QueryLibraryDisplayPreference = {
  mode: QueryLibraryDisplayMode
  properties: QueryLibraryDisplayProperty[]
}

function isDisplayMode(value: unknown): value is QueryLibraryDisplayMode {
  return QUERY_LIBRARY_DISPLAY_MODES.some((option) => option.value === value)
}

function isDisplayProperty(
  value: unknown
): value is QueryLibraryDisplayProperty {
  return QUERY_LIBRARY_DISPLAY_PROPERTIES.some(
    (option) => option.value === value
  )
}

/** A stored (or otherwise supplied) rows choice falls back to the default card. */
function resolveDisplayMode(value: unknown): QueryLibraryDisplayMode {
  return isDisplayMode(value) && value !== 'rows'
    ? value
    : QUERY_LIBRARY_DEFAULT_DISPLAY_MODE
}

/**
 * The user's last Display choices (view mode and visible properties). Filter
 * choices live in the URL and are deliberately untouched here.
 */
export function readQueryLibraryDisplayPreference(): QueryLibraryDisplayPreference | null {
  try {
    const raw = localStorage.getItem(DISPLAY_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    const properties = Array.isArray(parsed?.properties)
      ? parsed.properties.filter(isDisplayProperty)
      : QUERY_LIBRARY_DEFAULT_DISPLAY_PROPERTIES
    return {
      mode: resolveDisplayMode(parsed?.mode),
      properties,
    }
  } catch {
    return null
  }
}

export function writeQueryLibraryDisplayPreference(
  preference: QueryLibraryDisplayPreference
): void {
  try {
    localStorage.setItem(DISPLAY_STORAGE_KEY, JSON.stringify(preference))
  } catch {
    // Persistence is a convenience; a blocked store must not break Display.
  }
}
