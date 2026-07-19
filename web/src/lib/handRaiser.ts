// Product-qualified-lead "hand-raiser" invitations (rdst-dma.4). RDST is free
// end-to-end; these quiet, once-ever invitations ride inside a celebrated
// result and offer a conversation about running Readyset at scale. Strategy
// and copy live in design/proposals/pql-moments.html.

// Where "Talk to an engineer" routes. One constant so every moment shares it
// and the destination can change without touching any surface.
export const HAND_RAISER_URL = 'https://readyset.io/contact?ref=rdst'

// The strong-signal moments. Each fires at most once per install, ever.
export type PqlSignal = 'cache_created' | 'fleet_audit' | 'retention_30d'

const storageKey = (signal: PqlSignal): string => `rdst.handraiser.${signal}`

// True once a signal's invitation has been shown or dismissed; it never
// returns. A localStorage failure (private mode, tests) degrades to "not seen"
// so a broken store cannot silently suppress a first-time invitation.
export function isHandRaiserSeen(signal: PqlSignal): boolean {
  try {
    return localStorage.getItem(storageKey(signal)) !== null
  } catch {
    return false
  }
}

// Record that a signal's invitation has been shown or dismissed.
export function markHandRaiserSeen(signal: PqlSignal): void {
  try {
    localStorage.setItem(storageKey(signal), new Date().toISOString())
  } catch {
    // A store that will not persist just means the invitation may show again.
  }
}
