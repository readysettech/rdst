import type { Input, WebContents } from 'electron'

// The renderer is laid out for the browser at 100%; the desktop shell shows
// it two zoom steps smaller so more of a page fits without scrolling. A
// person's own zoom changes (Cmd/Ctrl +/-) persist per origin on top of this.
export const DEFAULT_ZOOM_FACTOR = 0.8

/** How often a held zoom key steps. */
const ZOOM_REPEAT_MS = 150

type Zoomable = Pick<
  WebContents,
  'getZoomLevel' | 'setZoomLevel' | 'setZoomFactor'
>

/**
 * Handles Cmd/Ctrl with =, +, - and 0 before menu accelerators, so unshifted Ctrl+= zooms
 * in as well as Ctrl++. 0 returns to the shell's actual size, as View > Actual Size does.
 * A held key repeats fast, so repeats step at most every ZOOM_REPEAT_MS.
 */
export function zoomShortcuts(
  contents: Zoomable,
  isMac: boolean,
  now: () => number = Date.now
): (event: { preventDefault: () => void }, input: Input) => void {
  let lastZoomAt = 0
  return (event, input) => {
    const modifier = isMac ? input.meta : input.control
    if (input.type !== 'keyDown' || !modifier || input.alt) return
    const direction =
      input.key === '=' || input.key === '+'
        ? 1
        : input.key === '-' || input.key === '_'
          ? -1
          : 0
    if (!direction && input.key !== '0') return
    event.preventDefault()
    const at = now()
    if (input.isAutoRepeat && at - lastZoomAt < ZOOM_REPEAT_MS) return
    lastZoomAt = at
    if (!direction) {
      contents.setZoomFactor(DEFAULT_ZOOM_FACTOR)
      return
    }
    contents.setZoomLevel(
      Math.max(-3, Math.min(5, contents.getZoomLevel() + direction * 0.5))
    )
  }
}
