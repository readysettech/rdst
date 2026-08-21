import type { SetupGuideStep } from '../../lib/analytics'

/**
 * The five setup signals, as reported by `GET /api/setup-progress`. Every
 * field is derived server-side from real state — a database connected from
 * the CLI arrives with `connected` already true — so the guide never stores a
 * "user did this" flag of its own. [RDST UX plan, D]
 */
export interface SetupProgress {
  target: string
  connected: boolean
  schema_built: boolean
  queries_found: boolean
  analyzed: boolean
  compared: boolean
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
  /** Search params the surface needs to land on the step itself. */
  search?: Record<string, string>
  done: boolean
}

type SetupStepDefinition = Omit<SetupStep, 'done' | 'search'> & {
  isDone: (progress: SetupProgress) => boolean
  /** The one query this step should act on, when the guide knows one. */
  searchFor?: (context: SetupStepContext) => Record<string, string> | undefined
}

/** What the guide knows beyond the five booleans. */
export interface SetupStepContext {
  /** Highest-impact query to analyze, for step 4's deep link. */
  analyzeHash?: string
}

const STEP_DEFINITIONS: SetupStepDefinition[] = [
  {
    id: 'connect-database',
    label: 'Connect a database',
    why: 'Readyset watches this database for queries.',
    to: '/onboarding',
    isDone: (progress) => progress.connected,
  },
  {
    id: 'build-schema',
    label: 'Build the schema',
    why: 'Gives Readyset your tables and relationships.',
    to: '/schema',
    isDone: (progress) => progress.schema_built,
  },
  {
    id: 'find-queries',
    label: 'Find your queries',
    why: 'Collects the queries this database actually runs.',
    to: '/queries',
    isDone: (progress) => progress.queries_found,
  },
  {
    id: 'analyze-query',
    label: 'Analyze a query',
    why: 'Shows why one query is slow.',
    // `/queries?analyze=<top hash>` opens the analyze drawer on the query with
    // the most database time behind it. Without a query to name — nothing
    // observed yet — the library itself is the honest destination.
    to: '/queries',
    searchFor: (context) =>
      context.analyzeHash ? { analyze: context.analyzeHash } : undefined,
    isDone: (progress) => progress.analyzed,
  },
  {
    id: 'compare',
    label: 'Compare origin vs Readyset',
    why: 'Proves the speedup on real traffic.',
    to: '/cache',
    isDone: (progress) => progress.compared,
  },
]

export const SETUP_STEP_COUNT = STEP_DEFINITIONS.length

export const SETUP_STEP_IDS: SetupGuideStep[] = STEP_DEFINITIONS.map(
  (step) => step.id
)

export function setupSteps(
  progress: SetupProgress,
  context: SetupStepContext = {}
): SetupStep[] {
  return STEP_DEFINITIONS.map(({ isDone, searchFor, ...step }) => ({
    ...step,
    search: searchFor?.(context),
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
 * Showing "0 of 5" on a failed read would be a lie and a nag, so the guide
 * stays hidden until it has a real answer.
 */
export function hasSetupSignal(
  progress: SetupProgress | undefined
): progress is SetupProgress {
  return Boolean(progress) && !progress?.error
}

/**
 * Routes that own the bottom-right corner or already are the step:
 * `/demo` runs the demo tour bubble in that exact slot, and `/onboarding`
 * IS step 1.
 */
export function isSetupGuideSuppressed(pathname: string): boolean {
  return (
    pathname === '/demo' ||
    pathname.startsWith('/demo/') ||
    pathname === '/onboarding' ||
    pathname.startsWith('/onboarding/')
  )
}
