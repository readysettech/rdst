import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Progress } from '@rs/ui-new/progress'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import type { ContainerProgress, Phase } from '../../lib/useDemo'
import { type ProvisionStep, provisionSteps } from './platformModel'

function StepMark({ state }: { state: ProvisionStep['state'] }) {
  if (state === 'done')
    return (
      <Icon
        name="tick"
        label=""
        className="h-3.5 w-3.5 justify-self-center text-content-layout-2"
      />
    )
  return (
    <span
      aria-hidden="true"
      className={cn(
        'block h-3.5 w-3.5 justify-self-center rounded-full border border-border-layout-2',
        state === 'active' && 'border-4 border-content-layout-1'
      )}
    />
  )
}

function StepRow({
  step,
  elapsed,
  percent,
}: {
  step: ProvisionStep
  /** Seconds the step has been active, or took when done. */
  elapsed?: number
  percent: number
}) {
  const active = step.state === 'active'
  return (
    <li
      aria-current={active ? 'step' : undefined}
      className={cn(
        'grid min-h-10 grid-cols-[18px_1fr_auto] items-center gap-3 border-t border-border-layout-2 text-body-medium text-content-layout-3 first:border-t-0',
        step.state === 'done' && 'text-content-layout-2',
        active && 'font-medium text-content-layout-1'
      )}
    >
      <StepMark state={step.state} />
      <span className="min-w-0 truncate">
        {step.label}
        {step.meta && (
          <span className="ml-1.5 font-normal text-content-layout-3">
            {step.meta}
          </span>
        )}
      </span>
      <span className="flex items-center gap-2.5 text-caption font-normal text-content-layout-3">
        {active && step.detail && (
          <span className="text-mono-small">{step.detail}</span>
        )}
        {!active && step.detail && <span>{step.detail}</span>}
        {active && step.id === 'images' && (
          <Progress
            tone="quiet"
            value={percent}
            label="Download progress"
            className="w-30"
          />
        )}
        {step.state === 'done' && elapsed != null && (
          <span className="tabular-nums">{elapsed} s</span>
        )}
      </span>
    </li>
  )
}

export interface StartingCardProps {
  containers: ContainerProgress[]
  phase: Phase
  loadRunning: boolean
  /** Overall provision progress, 0 to 100. */
  percent: number
  /** When the start began, in ms since the epoch. */
  startedAt: number
}

/** The facts card while the demo starts: six steps, the active one with its progress. */
export function StartingCard({
  containers,
  phase,
  loadRunning,
  percent,
  startedAt,
}: StartingCardProps) {
  const steps = provisionSteps(containers, phase, loadRunning)
  const activeIndex = steps.findIndex((step) => step.state === 'active')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  // Each step's finish time is pinned the first time it reads done, so the
  // durations on done rows hold still; steps already done at mount count as
  // finished when the start began.
  const [finishedAt, setFinishedAt] = useState<Record<string, number>>(() =>
    Object.fromEntries(
      steps
        .filter((step) => step.state === 'done')
        .map((step) => [step.id, startedAt])
    )
  )
  useEffect(() => {
    const missing = steps.filter(
      (step) => step.state === 'done' && finishedAt[step.id] == null
    )
    if (!missing.length) return
    const stamp = Date.now()
    setFinishedAt((current) => ({
      ...current,
      ...Object.fromEntries(missing.map((step) => [step.id, stamp])),
    }))
  }, [steps, finishedAt])
  const elapsedS = Math.max(0, Math.floor((now - startedAt) / 1000))
  const durationOf = (index: number) => {
    const step = steps[index]
    const end = finishedAt[step.id]
    if (end == null) return undefined
    const previous = index > 0 ? finishedAt[steps[index - 1].id] : startedAt
    return Math.max(0, Math.round((end - (previous ?? startedAt)) / 1000))
  }
  return (
    <section
      aria-labelledby="platform-starting-title"
      data-testid="platform-starting-card"
      className="mt-7 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 py-5.5"
    >
      <div className="flex items-baseline gap-3.5">
        <h2
          id="platform-starting-title"
          className="text-headline-5 text-content-layout-1"
        >
          Starting the demo
        </h2>
        <Text as="span" level="caption" className="text-content-layout-3">
          step {activeIndex < 0 ? steps.length : activeIndex + 1} of{' '}
          {steps.length}
        </Text>
        <Text
          as="span"
          level="caption"
          className="ml-auto tabular-nums text-content-layout-3"
        >
          {elapsedS} s
        </Text>
      </div>
      <ol className="mt-4 list-none p-0">
        {steps.map((step, index) => (
          <StepRow
            key={step.id}
            step={step}
            elapsed={durationOf(index)}
            percent={percent}
          />
        ))}
      </ol>
    </section>
  )
}
