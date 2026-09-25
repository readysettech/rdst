// Pure view model for the prerequisites checker: one overall phase, row order,
// and the copy each phase shows.
import type { PrereqCheck, PrereqResult, PrereqStatus } from './api'

export type CheckerPhase =
  /** No answer yet. */
  | 'connecting'
  /** RDST couldn't connect or the request failed. */
  | 'unreachable'
  /** At least one blocking check fails. */
  | 'blocked'
  /** Monitoring can start, but a non-blocking check failed (hidden SQL). */
  | 'attention'
  /** Everything monitoring needs passes (recommendations may remain). */
  | 'ready'

export function checkerPhase(
  result: PrereqResult | undefined,
  failed: boolean
): CheckerPhase {
  if (!result) return failed ? 'unreachable' : 'connecting'
  if (result.connection_error || result.checks.length === 0)
    return 'unreachable'
  if (!result.ready) return 'blocked'
  return result.attention > 0 ? 'attention' : 'ready'
}

/** Polling cadence: fast while the user is fixing things, off once done. */
export function pollInterval(
  phase: CheckerPhase,
  connectionError?: string | null
): number | false {
  if (phase === 'unreachable' && connectionError?.startsWith('SSH '))
    return false
  switch (phase) {
    case 'ready':
      return false
    case 'unreachable':
      return 8000
    default:
      return 4000
  }
}

export const isDone = (status: PrereqStatus) => status === 'ok'

export function isProblem(check: PrereqCheck) {
  return check.status === 'missing' || check.status === 'unknown'
}

/** Recommendations only improve capture; older answers mark them non-blocking warns. */
export function isRecommendation(check: PrereqCheck) {
  return check.recommended ?? (!check.blocking && check.status === 'warn')
}

/**
 * pg_stat_statements is two requirements with different fixes: loaded at server start (a
 * restart) and created in the database (one statement). Passing, they read as one row;
 * either failing shows both, each with its own fix.
 */
function mergeStatementChecks(checks: PrereqCheck[]): PrereqCheck[] {
  const preload = checks.find(
    (check) => check.id === 'pg_stat_statements_preload'
  )
  const created = checks.find((check) => check.id === 'pg_stat_statements')
  if (
    !preload ||
    !created ||
    preload.status !== 'ok' ||
    created.status !== 'ok'
  )
    return checks
  return checks.flatMap((check) =>
    check === preload
      ? []
      : check === created
        ? [{ ...created, label: 'pg_stat_statements is ready', detail: '' }]
        : [check]
  )
}

/** What monitoring needs first, in backend order; recommendations after. */
export function orderedChecks(all: PrereqCheck[]) {
  const checks = mergeStatementChecks(all)
  const required = checks.filter((check) => !isRecommendation(check))
  const optional = checks.filter(isRecommendation)
  return { required, optional }
}

export function progress(checks: PrereqCheck[]) {
  const { required } = orderedChecks(checks)
  return {
    done: required.filter((check) => check.status === 'ok').length,
    total: required.length,
    fixes: required.filter(isProblem).length,
  }
}

/** The check whose fix opens by default: the first failing one. */
export function firstOpenFix(checks: PrereqCheck[]): string | null {
  const { required, optional } = orderedChecks(checks)
  const first =
    required.find((check) => isProblem(check) && check.fix) ??
    optional.find((check) => isProblem(check) && check.fix)
  return first?.id ?? null
}

export function headline(
  phase: CheckerPhase,
  result: PrereqResult | undefined,
  target: string
): { title: string; body: string } {
  const count = result
    ? progress(result.checks)
    : { done: 0, total: 0, fixes: 0 }
  switch (phase) {
    case 'connecting':
      return {
        title: `Checking ${target}`,
        body: 'Reading the server settings RDST needs to see your queries.',
      }
    case 'unreachable':
      return {
        title: `Can't reach ${target}`,
        body: 'RDST keeps trying every few seconds.',
      }
    case 'blocked': {
      const { fixes } = count
      return {
        title:
          fixes === 0
            ? 'Checking the rest'
            : fixes === 1
              ? 'One fix needed'
              : `${fixes} fixes needed`,
        body: 'Make the change in another window. This page checks again every few seconds.',
      }
    }
    case 'attention':
      return {
        title: 'Monitoring can start',
        body: 'RDST can time every query, but some SQL from other users stays hidden until you grant access.',
      }
    case 'ready':
      return {
        title: 'Ready to monitor',
        body: `RDST can see every query ${target} runs.`,
      }
  }
}

export function statusWord(check: PrereqCheck): string {
  switch (check.status) {
    case 'ok':
      return 'Passed'
    case 'missing':
      return isRecommendation(check) ? 'Recommended' : 'Needs a fix'
    case 'warn':
      return 'Recommended'
    case 'waiting':
      return 'Waiting'
    default:
      return 'Not checked'
  }
}
