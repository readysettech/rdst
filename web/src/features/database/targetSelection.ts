import { storeSelectedTarget } from './selectedTarget'

/** Sets the global target from outside React (route redirects, `?target=` links). */
export function selectGlobalTarget(name: string) {
  const target = name.trim()
  if (!target) return
  storeSelectedTarget(target)
}
