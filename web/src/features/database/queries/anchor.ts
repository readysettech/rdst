// Keeps a row where the pointer left it while rows above it open or close: the page
// scrolls by exactly how far the row moved, frame by frame, until the height
// animations settle. No jump, no smooth-scroll fight.

export function scrollParent(element: HTMLElement): HTMLElement | null {
  let node = element.parentElement
  while (node) {
    const style = getComputedStyle(node)
    if (
      /(auto|scroll)/.test(style.overflowY) &&
      node.scrollHeight > node.clientHeight
    )
      return node
    node = node.parentElement
  }
  return null
}

/** Call right before a layout change; keeps `#query-<key>` still for `ms`. */
export function anchorRow(key: string, ms = 500) {
  if (typeof window === 'undefined') return
  const element = document.getElementById(`query-${key}`)
  if (!element) return
  const parent = scrollParent(element)
  const startTop = element.getBoundingClientRect().top
  const until = performance.now() + ms
  // The person scrolling takes over: the row stops holding still.
  let released = false
  const release = () => {
    released = true
  }
  const inputs = ['wheel', 'touchmove', 'keydown'] as const
  for (const type of inputs)
    window.addEventListener(type, release, { passive: true, once: true })
  const done = () => {
    for (const type of inputs) window.removeEventListener(type, release)
  }
  const step = () => {
    const current = document.getElementById(`query-${key}`)
    if (!current || released) return done()
    const delta = current.getBoundingClientRect().top - startTop
    if (Math.abs(delta) > 0.5) {
      if (parent) parent.scrollTop += delta
      else window.scrollBy(0, delta)
    }
    if (performance.now() < until) window.requestAnimationFrame(step)
    else done()
  }
  window.requestAnimationFrame(step)
}
