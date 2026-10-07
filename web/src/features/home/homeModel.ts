import type { InboxItem, MuteRule } from '../../lib/monitor/types'
import { inboxCounts, itemPlace } from '../inbox/inboxModel'
import { countsAsUnread } from '../monitor-kit/severity'

/** A database card's health glow. Neutral is a database without monitoring. */
export type HealthGlow = 'positive' | 'warning' | 'negative' | 'neutral'

export interface DatabaseStanding {
  monitored: boolean
  /** Monitoring is on but failing: paused, or the database can't be reached. */
  failing: boolean
  urgent: number
  watch: number
  openActions: number
}

/**
 * Red when the database has an open Urgent Inbox item or monitoring is failing, orange
 * when it has open actions or Watch items, green otherwise.
 */
export function healthGlow(standing: DatabaseStanding): HealthGlow {
  if (!standing.monitored) return 'neutral'
  if (standing.failing || standing.urgent > 0) return 'negative'
  if (standing.openActions > 0 || standing.watch > 0) return 'warning'
  return 'positive'
}

export const GLOW_LABEL: Record<HealthGlow, string> = {
  negative: 'Urgent',
  warning: 'Worth a look',
  positive: 'All clear',
  neutral: 'Monitoring off',
}

/** One database's open Inbox numbers: the Inbox's own counts, and the unread ones. */
export function inboxStanding(
  items: InboxItem[],
  rules: MuteRule[] | undefined,
  target: string
) {
  const own = items.filter((item) => item.target === target)
  const counts = inboxCounts(own, rules)
  const fresh = own.filter(
    (item) => countsAsUnread(item) && itemPlace(item) !== 'resolved'
  ).length
  return { urgent: counts.actNow, watch: counts.watch, fresh }
}
