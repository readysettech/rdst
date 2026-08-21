import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { useTarget } from '../../hooks/useTarget'
import { type SetupGuideStep, trackEvent } from '../../lib/analytics'
import {
  hasSetupSignal,
  isSetupComplete,
  SETUP_STEP_IDS,
  type SetupProgress,
  setupSteps,
} from './setupModel'

/** Modest cadence: setup steps complete on human timescales, not machine ones. */
const SETUP_POLL_MS = 60_000

export const setupProgressQueryKey = (target: string | null) =>
  ['setup-progress', target ?? ''] as const

export async function fetchSetupProgress(
  target?: string | null
): Promise<SetupProgress> {
  const query = target ? `?target=${encodeURIComponent(target)}` : ''
  const response = await fetch(`/api/setup-progress${query}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch setup progress: ${response.status}`)
  }
  return response.json()
}

/**
 * The five setup signals for the active target. Refetches on window focus so
 * a step completed in a terminal turns green on return, and polls only while
 * the guide is on screen with work left — a finished setup polls nothing.
 */
export function useSetupProgress({ poll = false }: { poll?: boolean } = {}) {
  const { target } = useTarget()
  return useQuery({
    queryKey: setupProgressQueryKey(target),
    queryFn: () => fetchSetupProgress(target),
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!poll || !hasSetupSignal(data) || isSetupComplete(data)) return false
      return SETUP_POLL_MS
    },
  })
}

// Server state is the truth; the event marks the moment this client observed
// the step flip. One event per step per session, so a refetch loop or a target
// switch back and forth cannot inflate the funnel.
const emitted = new Set<SetupGuideStep>()

/** Test seam: clears the per-session step-completion dedupe. */
export function __resetSetupProgressEventsForTests(): void {
  emitted.clear()
}

/**
 * Emit `setup_step_completed` when a step is observed going from incomplete to
 * complete. The first reading is a baseline, never a burst of events for steps
 * that were already done when the app opened.
 */
export function useSetupStepCompletionEvents(
  progress: SetupProgress | undefined
): void {
  const previous = useRef<Record<string, boolean> | null>(null)

  useEffect(() => {
    if (!hasSetupSignal(progress)) return
    const current: Record<string, boolean> = {}
    for (const step of setupSteps(progress)) current[step.id] = step.done

    const before = previous.current
    previous.current = current
    if (!before) return

    for (const step of SETUP_STEP_IDS) {
      if (!current[step] || before[step] || emitted.has(step)) continue
      emitted.add(step)
      trackEvent('setup_step_completed', { step })
    }
  }, [progress])
}
