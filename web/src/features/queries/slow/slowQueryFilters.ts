import type { TopMode } from '../../../types/top'

export interface SlowQueryFiltersSnapshot {
  mode: TopMode
  source: string
  sort: string
  limit: number
  filterPattern: string
  duration: number
  autoSave: boolean
  minFreq: number
  minLoadPct: number
}

export const slowQueryLastFiltersQueryKey = (target: string) =>
  ['top', 'lastFilters', target] as const

export function getSlowQueryFilterError(
  pattern: string,
  mode: TopMode
): string | null {
  if (mode === 'realtime' || !pattern.trim()) return null

  try {
    new RegExp(pattern)
    return null
  } catch (error) {
    return error instanceof Error ? error.message : 'Invalid regular expression'
  }
}
