// Spans with no readings, and why (Round 5): RDST couldn't reach the database (a
// lost-connection episode the backend recorded) or no known cause, such as RDST itself
// not running. Charts draw each as a hatched band with its label.
import type { MonitorOutage } from '@/lib/monitor/types'

export type PauseCause = 'unreachable' | 'unknown'

export interface ChartPause {
  from: number
  to: number
  cause: PauseCause
  /** Still going on: the band runs to now. */
  ongoing: boolean
  /** The lost-connection inbox item, when there is one. */
  itemId?: number
}

export const PAUSE_LABEL: Record<PauseCause, string> = {
  unreachable: "Monitoring paused: RDST couldn't reach the database",
  unknown: 'No readings',
}

/** Removes every `cut` span from [from, to); what's left, in order. */
function subtract(
  from: number,
  to: number,
  cuts: { from: number; to: number }[]
): { from: number; to: number }[] {
  let pieces = [{ from, to }]
  for (const cut of cuts) {
    pieces = pieces.flatMap((piece) => {
      if (cut.to <= piece.from || cut.from >= piece.to) return [piece]
      const out = []
      if (cut.from > piece.from) out.push({ from: piece.from, to: cut.from })
      if (cut.to < piece.to) out.push({ from: cut.to, to: piece.to })
      return out
    })
  }
  return pieces
}

/**
 * The pauses inside `domain`. `readings` are the bucket starts that hold a reading;
 * a stretch longer than three buckets between two readings is a gap. Outages are drawn
 * whole (an ongoing one runs to `now`); the rest of a gap has no known cause.
 */
export function chartPauses({
  readings,
  res,
  domain,
  outages = [],
  now,
}: {
  readings: number[]
  res: number
  domain: [number, number]
  outages?: MonitorOutage[]
  now: number
}): ChartPause[] {
  const [d0, d1] = domain
  const known = outages.map((outage) => ({
    from: outage.start,
    to: outage.end ?? now,
    ongoing: outage.end == null,
    itemId: outage.item_id,
  }))
  const pauses: ChartPause[] = known.map((span) => ({
    ...span,
    cause: 'unreachable',
  }))
  const sorted = [...readings].sort((a, b) => a - b)
  for (let i = 1; i < sorted.length; i++) {
    const from = sorted[i - 1] + res
    const to = sorted[i]
    if (to - from <= 3 * res) continue
    for (const piece of subtract(from, to, known))
      if (piece.to - piece.from > 3 * res)
        pauses.push({ ...piece, cause: 'unknown', ongoing: false })
  }
  return pauses
    .map((pause) => ({
      ...pause,
      from: Math.max(pause.from, d0),
      to: Math.min(pause.to, d1),
    }))
    .filter((pause) => pause.to > pause.from)
    .sort((a, b) => a.from - b.from)
}

export function pauseAt(
  pauses: ChartPause[] | undefined,
  x: number
): ChartPause | null {
  return pauses?.find((pause) => x >= pause.from && x <= pause.to) ?? null
}
