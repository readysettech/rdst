// Tells a hover the person made from one the page made by scrolling content under a
// parked pointer. Scrolling fires enter and leave events at the same coordinates the
// pointer last had; a hover counts once the pointer itself moves.

interface Point {
  x: number
  y: number
}

let last: Point | null = null
let parked: Point | null = null
let installed = false

function install() {
  if (installed || typeof window === 'undefined') return
  installed = true
  window.addEventListener(
    'pointermove',
    (event) => {
      if (parked && (event.clientX !== parked.x || event.clientY !== parked.y))
        parked = null
      last = { x: event.clientX, y: event.clientY }
    },
    { capture: true, passive: true }
  )
  window.addEventListener(
    'scroll',
    () => {
      if (last) parked = last
    },
    { capture: true, passive: true }
  )
}

install()

/** True when the pointer hasn't moved since the page last scrolled under it. */
export function fromScroll(event: { clientX: number; clientY: number }) {
  return (
    parked != null && event.clientX === parked.x && event.clientY === parked.y
  )
}

/** Test-only: forget the tracked pointer. */
export function resetPointerIntent() {
  last = null
  parked = null
}
