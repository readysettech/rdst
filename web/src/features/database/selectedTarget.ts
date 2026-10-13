// The selected database. The backend's preferences hold it, so a new browser or app profile
// starts on the same one; localStorage keeps it for the next page load, and a change event
// switches every mounted useTarget (and other tabs, through the storage event) at once.
export const TARGET_STORAGE_KEY = 'rdst_selected_target'
export const TARGET_CHANGED_EVENT = 'rdst_target_changed'
const PREFERENCES_URL = '/api/settings/preferences'

// Whether the backend's selection has been read this page load. Until then a selection is
// held back: pages fill an empty one with a default before the backend has answered, which
// the backend's saved choice replaces; a choice the user made (a link, a click) replaces it.
let hydrated = false
let pending: { target: string | null; explicit: boolean } | undefined

export function readSelectedTarget(): string | null {
  try {
    return window.localStorage.getItem(TARGET_STORAGE_KEY) || null
  } catch {
    return null
  }
}

function writeLocal(target: string | null) {
  try {
    if (target) window.localStorage.setItem(TARGET_STORAGE_KEY, target)
    else window.localStorage.removeItem(TARGET_STORAGE_KEY)
  } catch {
    /* storage blocked: the event below still switches mounted pages */
  }
  window.dispatchEvent(
    new CustomEvent<string | null>(TARGET_CHANGED_EVENT, { detail: target })
  )
}

function persist(target: string | null, explicit: boolean) {
  if (!hydrated) {
    if (explicit || !pending?.explicit) pending = { target, explicit }
    return
  }
  void fetch(PREFERENCES_URL, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected_target: target ?? '' }),
  }).catch(() => undefined)
}

/**
 * Select a database (or none) here, in every tab, and in the backend's preferences.
 * `automatic` marks a default the app filled in rather than the user's choice.
 */
export function storeSelectedTarget(name: string | null, automatic = false) {
  if (typeof window === 'undefined') return
  const target = name?.trim() || null
  writeLocal(target)
  persist(target, !automatic)
}

/**
 * Apply the backend's selection once per page load. A choice the user made while it loaded
 * wins and is saved; otherwise the backend's wins over this browser's. With none saved yet,
 * this browser's selection is saved. Undefined means the backend doesn't keep one.
 */
export function hydrateSelectedTarget(fromBackend: string | null | undefined) {
  if (hydrated || typeof window === 'undefined') return
  hydrated = true
  const held = pending
  pending = undefined
  if (fromBackend === undefined) return
  if (held?.explicit) {
    if (held.target !== readSelectedTarget()) writeLocal(held.target)
    if (held.target !== fromBackend) persist(held.target, true)
    return
  }
  if (fromBackend) {
    if (fromBackend !== readSelectedTarget()) writeLocal(fromBackend)
    return
  }
  const local = held ? held.target : readSelectedTarget()
  if (local) persist(local, true)
}
