// AI monitoring vs local monitoring: the words for the account's limit and for one
// database's state. The limit comes from the Key Service (GET /api/monitor/limit);
// nothing here assumes a number.
import type {
  AiMonitoringReason,
  AiMonitoringState,
  MonitoringLimit,
} from '@/lib/monitor/types'

/** "AI monitoring on 2 of 3 databases", or without a known limit, "on 2 databases". */
export function limitLine(limit: MonitoringLimit | undefined): string | null {
  if (!limit) return null
  const noun = (n: number) => (n === 1 ? 'database' : 'databases')
  if (limit.limit == null)
    return `AI monitoring on ${limit.in_use} ${noun(limit.in_use)}`
  return `AI monitoring on ${limit.in_use} of ${limit.limit} ${noun(limit.limit)}`
}

/** Which key sets the limit, said once under the count. */
export function limitBasis(limit: MonitoringLimit | undefined): string | null {
  if (!limit || limit.limit == null) return null
  return limit.own_key
    ? 'Your own Anthropic key raises the limit.'
    : 'Contact us if you need more.'
}

export interface AiStateCopy {
  label: string
  description: string
  tone: 'positive' | 'warning' | 'neutral'
}

/** One database's AI state, so local monitoring never reads as AI monitoring. */
export function aiStateCopy(state: AiMonitoringState | undefined): AiStateCopy {
  switch (state) {
    case 'local':
      return {
        label: 'Local monitoring only',
        description:
          "Your account's AI slots are all in use. Charts and alerts keep running.",
        tone: 'warning',
      }
    case 'paused':
      return {
        label: 'AI paused: out of AI usage',
        description:
          'AI checks, deep analysis and reports are paused until your usage is raised. Charts and alerts keep running.',
        tone: 'warning',
      }
    case 'pending':
      return {
        label: 'AI monitoring starting',
        description:
          'RDST tries again every minute. Charts and alerts already run.',
        tone: 'neutral',
      }
    case 'off':
      return {
        label: 'Monitoring off',
        description: 'Nothing runs in the background for this database.',
        tone: 'neutral',
      }
    default:
      return {
        label: 'AI monitoring',
        description:
          'Charts, alerts, AI checks, deep analysis and nightly reports.',
        tone: 'positive',
      }
  }
}

/** Said wherever monitoring runs without AI for a database, next to the way to fix it. */
export const NO_AI_WARNING =
  "You won't get automatic AI recommendations for this database."

/** How to free a slot, for a full account. */
export const FREE_A_SLOT =
  'To free a slot, turn off monitoring on another database, here or on another computer signed in to the same account.'

/**
 * What the turn-on dialog says about AI for this database. `aiOff` when the account is
 * full and doesn't hold a slot for it: the dialog then turns on local monitoring.
 */
export function enableAiCopy(limit: MonitoringLimit | undefined): {
  aiOff: boolean
  buttonLabel: string | null
  title: string | null
  message: string | null
} {
  if (!limit || limit.target_fits !== false || limit.limit == null)
    return { aiOff: false, buttonLabel: null, title: null, message: null }
  return {
    aiOff: true,
    buttonLabel: 'Turn on local monitoring',
    title: `AI monitoring is off for this database: ${limit.in_use} of ${limit.limit} are in use`,
    message: `Charts and alerts still run. ${FREE_A_SLOT}`,
  }
}

/** The ask to contact us when the account's AI limit is used up, or null. */
export function allowanceCopy(limit: MonitoringLimit | undefined): {
  title: string
  message: string
  contact: string
} | null {
  if (!limit?.allowance?.length) return null
  const what =
    limit.allowance.length === 2
      ? 'AI checks, deep analysis and reports'
      : limit.allowance[0] === 'jev'
        ? 'AI checks'
        : 'deep analysis and reports'
  return {
    title: "You've run out of AI usage for now",
    message: `${what[0].toUpperCase()}${what.slice(1)} are paused until your usage is raised. Charts and alerts keep running.`,
    contact: limit.contact ?? '',
  }
}

/**
 * The one vocabulary for "is this database monitored, and is AI on?". Every surface that
 * shows a database's monitoring state (Database header, Manage targets, the target
 * switcher) reads it from monitoringStatus() so the words, tones and actions match.
 *
 * - ai_on: monitoring on and AI holds a slot.
 * - ai_starting: monitoring on, AI asked for and not answered yet (first minute only).
 * - local_only: monitoring on, AI off, with a reason.
 * - paused: monitoring on, but RDST can't reach the database, so readings are paused.
 * - off: monitoring off.
 */
export type MonitoringStatusKind =
  | 'ai_on'
  | 'ai_starting'
  | 'local_only'
  | 'paused'
  | 'off'

/** Why AI monitoring is off. `not_started`: pending past the grace period, no known cause. */
export type AiOffReason =
  | 'signed_out'
  | 'unreachable'
  | 'service_error'
  | 'limit'
  | 'quota'
  | 'not_started'

/** What the user can do about the state. Callers wire the ones that need their context. */
export type MonitoringActionKind =
  | 'turn_on'
  | 'sign_in'
  | 'free_slot'
  | 'contact'
  | 'retry'
  | 'check_connection'

export type MonitoringTone = 'positive' | 'warning' | 'negative' | 'neutral'

export interface MonitoringStatus {
  kind: MonitoringStatusKind
  /** "AI monitoring on", "Local monitoring only", "Monitoring paused", "Monitoring off". */
  label: string
  /** Short form for tight lists: "AI on", "Local only", "Paused", "Off", "Starting". */
  short: string
  /** For local_only: "AI off: you're signed out". Null otherwise. */
  reasonLabel: string | null
  reason: AiOffReason | null
  /** For local_only: what to do, as a short title ("Sign in to turn on AI monitoring"). */
  headline: string | null
  /** One plain sentence on what runs and why. Always shown, never only on hover. */
  detail: string
  action: { kind: MonitoringActionKind; label: string } | null
  tone: MonitoringTone
  /** Something is in motion (starting up): animate the dot. */
  busy: boolean
}

export interface MonitoringStatusInput {
  enabled: boolean | undefined
  ai: AiMonitoringState | undefined
  reason?: AiMonitoringReason | null
  /** When the current AI state began, epoch seconds. */
  since?: number | null
  /** Monitoring is on but the database can't be reached. */
  connectionLost?: boolean
  /** Epoch seconds; defaults to the wall clock. */
  now?: number
}

/** How long "AI monitoring starting" may show before RDST says why it hasn't started. */
export const AI_STARTING_GRACE_S = 60

/** Reasons that clear by themselves: RDST retries, and nothing is asked of the user. */
const TRANSIENT_REASONS: ReadonlySet<AiOffReason> = new Set([
  'unreachable',
  'service_error',
  'not_started',
])

const REASON_COPY: Record<
  AiOffReason,
  {
    label: string
    short: string
    reasonLabel: string
    headline: string
    detail: string
    action: MonitoringStatus['action']
  }
> = {
  signed_out: {
    label: 'AI off: signed out',
    short: 'Signed out',
    reasonLabel: "you're signed out",
    headline: 'Sign in to turn on AI monitoring',
    detail:
      'Sign in to get AI checks, deep analysis and nightly reports. Charts and alerts keep running.',
    action: { kind: 'sign_in', label: 'Sign in' },
  },
  limit: {
    label: 'Local monitoring only',
    short: 'Local only',
    reasonLabel: 'all AI slots in use',
    headline: 'Make an AI slot available to turn on AI here',
    detail:
      "Your account's AI slots are all in use. Charts and alerts keep running.",
    action: { kind: 'free_slot', label: 'Manage databases' },
  },
  quota: {
    label: 'AI paused: out of usage',
    short: 'AI paused',
    reasonLabel: 'out of AI usage',
    headline: "You've run out of AI usage for now",
    detail:
      'AI checks, deep analysis and reports are paused until your usage is raised. Charts and alerts keep running.',
    action: { kind: 'contact', label: 'Contact us' },
  },
  unreachable: {
    label: 'AI reconnecting',
    short: 'Reconnecting',
    reasonLabel: "can't reach Readyset",
    headline: "RDST can't reach Readyset right now",
    detail:
      "RDST can't reach Readyset right now and tries again every minute. Charts and alerts keep running.",
    action: { kind: 'retry', label: 'Try again' },
  },
  service_error: {
    label: 'AI reconnecting',
    short: 'Reconnecting',
    reasonLabel: 'Readyset had a problem',
    headline: "Readyset couldn't start AI monitoring",
    detail:
      "Readyset's AI service returned an error. RDST tries again every minute.",
    action: { kind: 'retry', label: 'Try again' },
  },
  not_started: {
    label: 'AI monitoring starting',
    short: 'Starting',
    reasonLabel: "hasn't started yet",
    headline: "AI monitoring hasn't started yet",
    detail: 'RDST tries again every minute. Charts and alerts already run.',
    action: { kind: 'retry', label: 'Try again' },
  },
}

/** AI is off for a reason the user has to act on (signed out, no slot, out of usage). */
export function needsUserAction(status: MonitoringStatus): boolean {
  return (
    status.kind === 'local_only' &&
    status.reason != null &&
    !TRANSIENT_REASONS.has(status.reason)
  )
}

/** One database's monitoring state, in the shared words. */
export function monitoringStatus(
  input: MonitoringStatusInput
): MonitoringStatus {
  const { enabled, ai, connectionLost } = input
  if (enabled === false || ai === 'off')
    return {
      kind: 'off',
      label: 'Monitoring off',
      short: 'Off',
      reasonLabel: null,
      reason: null,
      headline: null,
      detail: 'Nothing runs in the background for this database.',
      action: { kind: 'turn_on', label: 'Turn on monitoring' },
      tone: 'neutral',
      busy: false,
    }
  if (connectionLost)
    return {
      kind: 'paused',
      label: 'Monitoring paused',
      short: 'Paused',
      reasonLabel: "Can't reach the database",
      reason: null,
      headline: null,
      detail:
        "RDST can't reach this database, so readings are paused. They pick back up by themselves once it answers.",
      action: { kind: 'check_connection', label: 'Check again' },
      tone: 'negative',
      busy: false,
    }
  if (ai === 'on' || ai === undefined)
    return {
      kind: 'ai_on',
      label: 'AI monitoring on',
      short: 'AI on',
      reasonLabel: null,
      reason: null,
      headline: null,
      detail:
        'Charts, alerts, AI checks, deep analysis and nightly reports are running.',
      action: null,
      tone: 'positive',
      busy: false,
    }
  const reason = aiOffReason(input)
  if (reason === null)
    return {
      kind: 'ai_starting',
      label: 'AI monitoring starting',
      short: 'Starting',
      reasonLabel: null,
      reason: null,
      headline: null,
      detail: 'Charts and alerts are running. AI joins in a moment.',
      action: null,
      tone: 'neutral',
      busy: true,
    }
  const copy = REASON_COPY[reason]
  const transient = TRANSIENT_REASONS.has(reason)
  return {
    kind: 'local_only',
    label: copy.label,
    short: copy.short,
    reasonLabel: copy.reasonLabel,
    reason,
    headline: copy.headline,
    detail: copy.detail,
    action: copy.action,
    tone: transient ? 'neutral' : 'warning',
    busy: transient,
  }
}

function aiOffReason(input: MonitoringStatusInput): AiOffReason | null {
  const { ai, reason, since } = input
  if (ai === 'local') return 'limit'
  if (ai === 'paused') return 'quota'
  // Signed out, a full account or a used-up allowance are known causes: say so at once.
  if (reason === 'signed_out' || reason === 'limit' || reason === 'quota')
    return reason
  const now = input.now ?? Date.now() / 1000
  const waited = since == null ? 0 : now - since
  if (waited < AI_STARTING_GRACE_S) return null
  return reason ?? 'not_started'
}

/** Tone to classes, so a state reads in the same colors everywhere. */
export const MONITORING_TONE_CLASSES: Record<
  MonitoringTone,
  { text: string; dot: string; surface: string; border: string }
> = {
  positive: {
    text: 'text-content-positive-soft',
    dot: 'bg-surface-positive-solid',
    surface: 'bg-surface-positive-soft',
    border: 'border-border-positive-soft',
  },
  warning: {
    text: 'text-content-warning-soft',
    dot: 'bg-surface-warning-solid',
    surface: 'bg-surface-warning-soft',
    border: 'border-border-warning-soft',
  },
  negative: {
    text: 'text-content-negative-soft',
    dot: 'bg-surface-negative-solid',
    surface: 'bg-surface-negative-soft',
    border: 'border-border-negative-soft',
  },
  neutral: {
    text: 'text-content-layout-2',
    dot: 'bg-content-layout-3',
    surface: 'bg-surface-layout-2',
    border: 'border-border-layout-1',
  },
}
