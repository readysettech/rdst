// An inbox item folded to one line: which queries, when it began, and whether it's still
// happening or which reading went back to normal (and when, as RDST measured it).
import type { InboxItem, RecurringNote } from '@/lib/monitor/types'
import { formatClock, plural } from '../monitor-kit/format'
import {
  formatStampTime,
  itemPlace,
  recoveryPhrase,
  timePhrase,
} from './inboxModel'
import { incidentWindow } from './incidentModel'
import { itemStory } from './itemStory'

export interface ItemLine {
  /** 'COUNT on orders', 'COUNT on orders and 5 more queries', or the target. */
  who: string
  /** 'since 9:42 PM', 'from 9:42 PM', 'by 9:42 PM'. */
  when: string | null
  /** 'Still happening', 'Latency back to normal at 11:25 PM', or null when unknown. */
  state: string | null
  ongoing: boolean
}

/**
 * Why the item is where it is. A live item's section already names its band and its line
 * says whether it's still happening, so only a closed item carries a prefix.
 */
export function itemReason(item: InboxItem): string | null {
  if (!item.band_reason) return null
  const reason = item.band_reason.replace(/^[^:]+: /, '')
  if (itemPlace(item) === 'resolved')
    return `${item.status === 'back_to_normal' || item.severity_band === 'recovered' ? 'Back to normal' : 'Closed'}: ${reason}`
  const bare = reason.replace(/,? still happening\.?$/i, '')
  return bare.charAt(0).toUpperCase() + bare.slice(1)
}

/** The one line a folded item shows under its title. */
export function itemLine(
  item: InboxItem,
  names: Map<string, string>,
  now = Date.now() / 1000
): ItemLine {
  const closed = itemPlace(item) === 'resolved'
  const story = itemStory(item, names, now)
  const [first] = story.queries
  const count = Math.max(story.queries.length, item.query_keys.length)
  const who = first
    ? count > 1
      ? `${first.name} and ${plural(count - 1, 'more query', 'more queries')}`
      : first.name
    : item.target
  // A repeating burst reads by its latest occurrence.
  const [latest] = item.occurrences ?? []
  if (item.type === 'query_stopped_running') {
    const window = incidentWindow(item)
    const ongoing =
      !closed &&
      (item.status
        ? item.status === 'ongoing'
        : (window?.ongoing ?? item.state !== 'resolved') &&
          item.state !== 'dismissed')
    const start =
      latest?.start ?? item.status_at ?? window?.start ?? item.created_at
    const end =
      item.status === 'back_to_normal'
        ? item.status_at
        : (latest?.end ?? window?.end)
    return {
      who,
      when: null,
      state: ongoing
        ? `Hasn't run since ${formatStampTime(start, now)}`
        : end != null
          ? recoveryPhrase(item, end, now)
          : null,
      ongoing,
    }
  }
  if (latest && (item.occurrences?.length ?? 0) > 1) {
    const at = formatStampTime(latest.start, now)
    // The backend reads the latest occurrence's own members; it decides when it says.
    const ongoing =
      !closed && (item.status ? item.status === 'ongoing' : latest.end == null)
    const end =
      latest.end ?? (item.status === 'back_to_normal' ? item.status_at : null)
    if (ongoing)
      return {
        who,
        when: `latest since ${at}`,
        state: 'Still happening',
        ongoing: true,
      }
    return {
      who,
      when: `latest from ${at}`,
      state: end != null ? recoveryPhrase(item, end, now) : null,
      ongoing: false,
    }
  }
  const window = incidentWindow(item)
  if (!window) return { who, when: null, state: null, ongoing: false }
  const start = window.measured ? formatStampTime(window.start, now) : null
  if (window.ongoing)
    return {
      who,
      when: start ? `since ${start}` : timePhrase('by', window.start, now),
      state: 'Still happening',
      ongoing: true,
    }
  return {
    who,
    when: start ? `from ${start}` : timePhrase('by', window.start, now),
    state: window.end != null ? recoveryPhrase(item, window.end, now) : null,
    ongoing: false,
  }
}

const trim = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 })

/** '45 minutes', '2 hours', '1.5 hours'. */
function periodText(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return plural(minutes, 'minute')
  const hours = seconds / 3600
  return hours === 1 ? 'hour' : `${trim(hours)} hours`
}

/**
 * 'This happens about every 45 minutes, and this time it's bigger than usual.' The
 * usual bursts stay quiet; the line explains why this one didn't.
 */
export function recurringText(note: RecurringNote): string {
  const when =
    note.kind === 'daily'
      ? `every day around ${formatClock(note.last_onset)}`
      : note.period_s >= 3600 && note.period_s % 3600 < 300
        ? `about every ${periodText(Math.round(note.period_s / 3600) * 3600)}`
        : `about every ${periodText(note.period_s)}`
  const why =
    note.verdict === 'bigger'
      ? "this time it's bigger than usual"
      : "this time it's lasting longer than usual"
  return `This happens ${when}, and ${why}.`
}

/** 'And 60 smaller queries also moved.' or null. */
export function smallerText(count: number | undefined): string | null {
  if (!count) return null
  return `${plural(count, 'smaller query', 'smaller queries')} also moved at the same time.`
}
