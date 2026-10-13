// How the window recovers when its renderer process dies: reload it, unless it keeps dying,
// then show a local page that offers a reload and the logs.
export const MAX_RELOADS = 3
export const RELOAD_WINDOW_MS = 60_000

export type RendererGoneAction =
  | { action: 'none' }
  | { action: 'reload'; reloads: number[] }
  | { action: 'error-page'; reloads: number[] }

/**
 * What to do when the renderer goes away. `reloads` holds the times of earlier automatic
 * reloads; the result carries the list to keep for the next time.
 */
export function rendererGoneAction(
  reason: string,
  reloads: readonly number[],
  now: number,
  maxReloads: number = MAX_RELOADS,
  windowMs: number = RELOAD_WINDOW_MS
): RendererGoneAction {
  if (reason === 'clean-exit') return { action: 'none' }
  const recent = reloads.filter((at) => now - at < windowMs)
  if (recent.length >= maxReloads)
    return { action: 'error-page', reloads: recent }
  return { action: 'reload', reloads: [...recent, now] }
}
