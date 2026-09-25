import type { SetupGuideStep } from '../../lib/analytics'

/**
 * What setup needs to know about the active target. `connected` and `target` come
 * from `GET /api/setup-progress`, derived server-side from real state, so a database
 * connected from the CLI arrives already done. `monitoring` is the target's monitor
 * `enabled` setting. Schema discovery runs on its own and is never a setup step.
 * [RDST UX plan, D]
 */
export interface SetupProgress {
  target: string
  connected: boolean
  /** Monitoring is on for `target`; undefined until the setting has been read. */
  monitoring?: boolean
  schema_built?: boolean
  queries_found?: boolean
  analyzed?: boolean
  compared?: boolean
  error?: string | null
}

export interface SetupStep {
  id: SetupGuideStep
  /** Plain user words; clicking the row does the step rather than explaining it. */
  label: string
  /** One reason this step exists, at most eight words. Shown while incomplete. */
  why: string
  /** The surface that completes the step. */
  to: string
  done: boolean
}

type SetupStepDefinition = Omit<SetupStep, 'done'> & {
  isDone: (progress: SetupProgress) => boolean
}

const STEP_DEFINITIONS: SetupStepDefinition[] = [
  {
    id: 'connect-database',
    label: 'Connect a database',
    why: 'RDST watches this database for slow queries.',
    to: '/onboarding',
    isDone: (progress) => progress.connected,
  },
  {
    id: 'turn-on-monitoring',
    label: 'Turn on monitoring',
    why: 'Reads query statistics. Never writes.',
    to: '/database',
    isDone: (progress) => progress.monitoring === true,
  },
]

export const SETUP_STEP_COUNT = STEP_DEFINITIONS.length

export const SETUP_STEP_IDS: SetupGuideStep[] = STEP_DEFINITIONS.map(
  (step) => step.id
)

export function setupSteps(progress: SetupProgress): SetupStep[] {
  return STEP_DEFINITIONS.map(({ isDone, ...step }) => ({
    ...step,
    done: isDone(progress),
  }))
}

export function completedSetupSteps(progress: SetupProgress): number {
  return STEP_DEFINITIONS.reduce(
    (count, step) => count + (step.isDone(progress) ? 1 : 0),
    0
  )
}

export function isSetupComplete(progress: SetupProgress): boolean {
  return completedSetupSteps(progress) === SETUP_STEP_COUNT
}

/**
 * A reported error means the signals are unknown, not that nothing is done.
 * Showing "0 of 2" on a failed read would be a lie and a nag, so the guide
 * stays hidden until it has a real answer.
 */
export function hasSetupSignal(
  progress: SetupProgress | undefined
): progress is SetupProgress {
  return Boolean(progress) && !progress?.error
}
