// The hero of the full analysis: the plan score gauge (a still ring while a run is
// under way), the query name with its provenance line and the two-clause verdict (the
// four steps while analyzing), and the three numbers at the right.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Verdict } from '@rs/ui-new/verdict'
import type { AnalyzingStep } from '@/features/database/analyze/analyzingSteps'
import type { AnalysisView } from '@/lib/monitor/types'
import { formatInt, scanWord } from './analysisView'
import { PerformanceScoreGauge } from './PerformanceScoreGauge'

const DASH = '\u2014'

function Steps({ steps }: { steps: AnalyzingStep[] }) {
  return (
    <ol
      aria-label="Analysis steps"
      className="mt-3.5 m-0 flex list-none flex-col gap-2 p-0 text-body-small text-content-layout-3"
    >
      {steps.map((step) => (
        <li
          key={step.label}
          data-state={step.state}
          className={cn(
            'flex items-center gap-2.5',
            step.state === 'now' && 'font-medium text-content-layout-1'
          )}
        >
          {step.state === 'done' ? (
            <Icon name="tick" label="" size="small" />
          ) : (
            <span
              className={cn(
                'h-3.5 w-3.5 shrink-0 rounded-full border border-border-layout-2',
                step.state === 'now' && 'border-4 border-content-layout-1'
              )}
            />
          )}
          {step.label}
        </li>
      ))}
    </ol>
  )
}

function Ring() {
  return (
    <div
      role="img"
      aria-label="Analyzing"
      className="flex h-33 w-50 flex-col items-center justify-center gap-3"
    >
      <svg
        viewBox="0 0 92 92"
        fill="none"
        strokeWidth="3"
        className="h-23 w-23"
      >
        <circle cx="46" cy="46" r="40" className="stroke-border-layout-2" />
        <path
          d="M46 6a40 40 0 0 1 40 40"
          strokeLinecap="round"
          className="stroke-content-layout-3"
        />
      </svg>
      <Text as="span" level="caption" className="text-content-layout-3">
        Analyzing
      </Text>
    </div>
  )
}

function Num({
  label,
  value,
  unit,
  detail,
}: {
  label: string
  value: string
  unit?: string
  detail: string | null
}) {
  return (
    <div>
      <Text as="p" level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <p className="mt-1 text-headline-2 tabular-nums text-content-layout-1">
        {value}
        {unit && (
          <span className="ml-1 text-caption font-normal text-content-layout-3">
            {unit}
          </span>
        )}
      </p>
      {detail && (
        <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
          {detail}
        </Text>
      )}
    </div>
  )
}

export interface AnalysisHeroProps {
  name: string
  view: AnalysisView
  /** "Estimated" or "Measured". */
  tag: string
  /** "planner only", "analyzed 9 min ago", "17% of database time". */
  meta: string[]
  meanMs: number | null
  /** While a run is under way: its steps replace the verdict. */
  steps: AnalyzingStep[] | null
}

export function AnalysisHero({
  name,
  view,
  tag,
  meta,
  meanMs,
  steps,
}: AnalysisHeroProps) {
  const running = steps !== null
  return (
    <div
      data-testid="analysis-hero"
      className="mt-2 grid grid-cols-[auto_1fr_auto] items-center gap-9"
    >
      {running ? (
        <Ring />
      ) : view.score != null ? (
        <PerformanceScoreGauge score={view.score} />
      ) : (
        <div className="h-33 w-50" />
      )}
      <div className="min-w-0">
        <Text as="h2" level="headline-2" className="text-content-layout-1">
          {name}
        </Text>
        <div className="mt-1.5 flex flex-wrap items-center gap-2.5 text-body-small text-content-layout-3">
          <Tag
            size="small"
            variant="neutral"
            modifier="outline"
            className="rounded-pill"
            label={tag}
          />
          {meta.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
        {running ? (
          <Steps steps={steps} />
        ) : (
          view.verdict && (
            <Verdict
              lead={view.verdict.lead}
              className="mt-3 max-w-[56ch] text-headline-5"
            >
              {view.verdict.rest}
            </Verdict>
          )
        )}
      </div>
      <div
        className={cn(
          'grid grid-cols-3 gap-x-9',
          running && 'pointer-events-none opacity-50'
        )}
      >
        <Num
          label="Rows read"
          value={formatInt(view.rows_read)}
          detail={
            view.rows_returned == null
              ? null
              : `to return ${formatInt(view.rows_returned)}`
          }
        />
        <Num
          label="Planner cost"
          value={formatInt(view.planner_cost)}
          detail={scanWord(view.explain_text)}
        />
        <Num
          label="Mean latency"
          value={
            meanMs == null
              ? DASH
              : meanMs < 10
                ? meanMs.toFixed(1)
                : String(Math.round(meanMs))
          }
          unit={meanMs == null ? undefined : 'ms'}
          detail={
            view.calls_today == null
              ? null
              : `${formatInt(view.calls_today)} calls today`
          }
        />
      </div>
    </div>
  )
}
