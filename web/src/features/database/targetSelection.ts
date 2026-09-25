// Sets the global target from outside React (route redirects, `?target=` links).
// Mirrors useTarget's storage key and change event so every mounted useTarget
// follows immediately.
const TARGET_STORAGE_KEY = 'rdst_selected_target'
const TARGET_CHANGED_EVENT = 'rdst_target_changed'

export function selectGlobalTarget(name: string) {
  const target = name.trim()
  if (!target || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(TARGET_STORAGE_KEY, target)
  } catch {
    /* storage blocked: the event below still switches mounted pages */
  }
  window.dispatchEvent(
    new CustomEvent<string | null>(TARGET_CHANGED_EVENT, { detail: target })
  )
}
