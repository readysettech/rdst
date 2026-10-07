// Small words for the Actions tab.

export function span(seconds: number): string {
  if (seconds >= 2 * 86400) return `${Math.floor(seconds / 86400)} days`
  if (seconds >= 2 * 3600) return `${Math.floor(seconds / 3600)} hours`
  if (seconds >= 120) return `${Math.floor(seconds / 60)} minutes`
  return 'a minute'
}

/** What the recommendations rest on: RDST's own readings, or the database's longer memory. */
export function basedOn(window: {
  observed_s: number
  counter_age_s: number
}): string {
  const observed = `Based on ${span(window.observed_s)} of traffic`
  return window.counter_age_s > window.observed_s * 2
    ? `${observed} and ${span(window.counter_age_s)} of index statistics`
    : observed
}

export function checkedAgo(at: number, now = Date.now() / 1000): string {
  const seconds = Math.max(0, now - at)
  if (seconds < 90) return 'checked just now'
  if (seconds < 3600) return `checked ${Math.floor(seconds / 60)} min ago`
  return `checked ${Math.floor(seconds / 3600)} h ago`
}

/** When a kind is checked next, as a countdown. */
export function nextCheck(at: number, now = Date.now() / 1000): string {
  const seconds = at - now
  return seconds <= 60
    ? 'Next check in under a minute'
    : `Next check in ${Math.ceil(seconds / 60)} min`
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

export function pct(share: number): string {
  const value = share * 100
  return value >= 10 ? `${value.toFixed(0)}%` : `${value.toFixed(1)}%`
}

export type Severity = 'low' | 'medium' | 'high'
export type Category =
  | 'indexes'
  | 'settings'
  | 'maintenance'
  | 'rewrites'
  | 'caching'

/** The Actions tab's kinds of change, in their default order. */
export const CATEGORIES: readonly { id: Category; label: string }[] = [
  { id: 'indexes', label: 'Indexes' },
  { id: 'settings', label: 'Server settings' },
  { id: 'maintenance', label: 'Maintenance' },
  { id: 'rewrites', label: 'Query rewrites' },
  { id: 'caching', label: 'Caching' },
]

/** How much a change matters, on one scale for every kind. */
export const SEVERITY: Record<
  Severity,
  { label: string; tag: 'negative' | 'warning' | 'neutral' }
> = {
  high: { label: 'High impact', tag: 'negative' },
  medium: { label: 'Worth doing', tag: 'warning' },
  low: { label: 'Small win', tag: 'neutral' },
}

/** Steps arrive one per line; a single line is a sentence, several are a numbered list. */
export function stepList(steps: string | null | undefined): string[] {
  return (steps ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

export type CardState = 'found' | 'checked' | 'checking' | 'waiting'

export interface CategoryStatus {
  state: CardState
  count: number
  /** One line under the label: what's there, or what RDST is doing. */
  line: string
  /** A quieter second line, like when it was last checked. */
  sub?: string
}

interface CoverageLike {
  state: CardState
  checked_at: number | null
  next_at?: number | null
}

/** A category card's count and status line, from its changes and what RDST checked. */
export function categoryStatus(
  kind: Category,
  cards: { type: string; severity: Severity }[],
  coverage: CoverageLike | undefined,
  progress: { analyzed: number; total: number },
  now = Date.now() / 1000
): CategoryStatus {
  const count = cards.length
  // Every kind says when it's looked at again, whether or not it has changes.
  const next = coverage?.next_at ? nextCheck(coverage.next_at, now) : undefined
  if (count) {
    if (kind === 'indexes') {
      const adds = cards.filter((card) => card.type === 'add_index').length
      const drops = cards.filter((card) => card.type === 'drop_index').length
      const parts = [
        adds ? `${adds} to add` : '',
        drops ? `${drops} to drop` : '',
      ].filter(Boolean)
      return { state: 'found', count, line: parts.join(', '), sub: next }
    }
    const high = cards.filter((card) => card.severity === 'high').length
    const medium = cards.filter((card) => card.severity === 'medium').length
    return {
      state: 'found',
      count,
      line: high
        ? `${high} high impact`
        : medium
          ? `${medium} worth doing`
          : `${count} small ${count === 1 ? 'win' : 'wins'}`,
      sub: next,
    }
  }
  const state = coverage?.state ?? 'checking'
  if (state === 'waiting')
    return {
      state,
      count: 0,
      line: progress.total
        ? `Waiting on deep analysis, ${Math.min(progress.analyzed, progress.total)} of ${progress.total}`
        : 'Waiting on deep analysis',
    }
  if (state === 'checking') return { state, count: 0, line: 'Checking now' }
  const at = coverage?.checked_at
  return {
    state: 'checked',
    count: 0,
    line: 'Nothing to change',
    sub: coverage?.next_at
      ? nextCheck(coverage.next_at, now)
      : at
        ? checkedAgo(at, now).replace(/^c/, 'C')
        : undefined,
  }
}
