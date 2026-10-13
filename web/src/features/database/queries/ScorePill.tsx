// The deep-analysis score of a query row: the number out of 100, else the rating's
// midpoint.
import type { MonitorQuery } from '@/lib/monitor/types'

const RATING_SCORE: Record<string, number> = {
  excellent: 95,
  good: 80,
  fair: 60,
  poor: 35,
}

export interface RowScore {
  /** 0-100 when the analysis carries a score. */
  score: number | null
  rating: string | null
}

/** The analysis's score or rating; null until it has one, so a row never says "Analyzed"
 *  over results that are still being written. */
export function rowScore(query: MonitorQuery): RowScore | null {
  const summary = query.last_analysis?.summary
  const rating = summary?.rating?.toLowerCase() ?? null
  const score =
    typeof summary?.efficiency_score === 'number'
      ? Math.round(summary.efficiency_score)
      : null
  return score == null && !rating ? null : { score, rating }
}

/** The score the tile shows: the number, else the rating's midpoint. */
export function scoreValue(score: RowScore): number | null {
  if (score.score != null) return score.score
  return score.rating ? (RATING_SCORE[score.rating] ?? null) : null
}
