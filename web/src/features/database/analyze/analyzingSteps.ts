// The four steps of a deep analysis as the pane lists them, from the phase monitoring
// reports or the progress of a run started here.
import type { AnalysisPhase } from '@/lib/monitor/types'

export type StepState = 'done' | 'now' | 'todo'

export interface AnalyzingStep {
  label: string
  state: StepState
}

export const STEP_LABELS = [
  'Reading the plan',
  'Sampling values',
  'Testing rewrites',
  'Writing the fix',
] as const

const PHASE_STEP: Record<AnalysisPhase, number> = {
  queued: 0,
  plan: 0,
  sample: 1,
  run: 2,
  schema: 3,
  advise: 3,
  verify: 3,
}

/** The steps with the current one marked; `at` is the 0-based step under way. */
export function stepsAt(at: number): AnalyzingStep[] {
  return STEP_LABELS.map((label, index) => ({
    label,
    state: index < at ? 'done' : index === at ? 'now' : 'todo',
  }))
}

export function stepsForPhase(phase: AnalysisPhase | null | undefined) {
  return stepsAt(phase ? PHASE_STEP[phase] : 0)
}

/** A run started here reports percent done; each quarter is one step. */
export function stepsForPercent(percent: number | null | undefined) {
  const value = Math.max(0, Math.min(99, percent ?? 0))
  return stepsAt(Math.floor(value / 25))
}

/** 0..1 of the line under the steps: a step done is a quarter, the current one half of its share. */
export function progressFraction(steps: AnalyzingStep[]): number {
  const done = steps.filter((step) => step.state === 'done').length
  const now = steps.some((step) => step.state === 'now') ? 0.5 : 0
  return Math.min(1, (done + now) / steps.length)
}
