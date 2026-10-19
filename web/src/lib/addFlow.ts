// One add-database attempt, from the dialog opening to monitoring on, whichever way the
// database arrives. The connection form and the agent path hand a new database to the
// turn-on dialog, which reads the attempt here, so both paths report one funnel. On the
// agent path the dialog also follows the agent's progress stream from here.
import {
  type DatabaseAddStage,
  type DatabaseAddVia,
  secondsSince,
  trackEvent,
} from './analytics'

export interface AddAttempt {
  /** When the add dialog opened, epoch ms. */
  openedAt: number
  /** Stage events already sent for this attempt. */
  sent: Set<string>
  /** Set while the dialog moves between its paths, so the view it leaves isn't abandoned. */
  switching: boolean
}

export const newAddAttempt = (): AddAttempt => ({
  openedAt: Date.now(),
  sent: new Set(),
  switching: false,
})

export interface AddFlow {
  attempt: AddAttempt
  via: DatabaseAddVia
  /** The agent prompt's issue time, epoch seconds: its progress stream replays from here. */
  agentSince?: number | null
}

const flows = new Map<string, AddFlow>()

/** A new database handed from an add path to the turn-on dialog. */
export function handOffAddFlow(target: string, flow: AddFlow): void {
  flows.set(target, flow)
}

export function addFlowFor(target: string | null | undefined): AddFlow | null {
  return (target && flows.get(target)) || null
}

/** The attempt ended: monitoring is on, or it was reported as abandoned. */
export function endAddFlow(target: string): void {
  flows.delete(target)
}

/** Runs `send` once per attempt for each key. */
export function onceForAttempt(
  attempt: AddAttempt,
  key: string,
  send: () => void
): void {
  if (attempt.sent.has(key)) return
  attempt.sent.add(key)
  send()
}

/** The attempt stopped at `stage` before monitoring was on. Once per attempt. */
export function abandonAttempt(
  attempt: AddAttempt,
  via: DatabaseAddVia,
  stage: DatabaseAddStage
): void {
  onceForAttempt(attempt, 'abandoned', () =>
    trackEvent('database_add_abandoned', {
      last_stage: stage,
      via,
      seconds_open: secondsSince(attempt.openedAt),
    })
  )
}
