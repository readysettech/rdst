import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { For } from '@rs/ui-new/for'
import { Icon } from '@rs/ui-new/icon'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { Progress } from '@rs/ui-new/progress'
import { Show } from '@rs/ui-new/show'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { HandRaiser } from '../../components/HandRaiser'
import type { MonitorHealth, MonitorQueryPage } from '../../lib/monitor/types'
import { fillCapturedParams } from '../../lib/sqlParameters'
import {
  CHART_READY_MINUTES,
  firstRun,
} from '../database/overview/firstReadingsModel'
import { historyLine, MiniTrend, SERIES_COLOR } from '../monitor-kit/charts'
import { DatabaseGlyph } from '../monitor-kit/DatabaseGlyph'
import { formatMsWithUnit, formatQps } from '../monitor-kit/format'
import type { MonitorPulseState } from '../monitor-kit/useMonitorPulse'
import { DemoBanner, DemoCard, JobCard } from './HomeCards'
import type { ContinueItem, HomeState, PortfolioCounts } from './homeModel'

const CONTINUE_TAG: Record<
  ContinueItem['kind'],
  { label: string; variant: 'primary' | 'informative' | 'positive' }
> = {
  asked: { label: 'asked', variant: 'primary' },
  analyzed: { label: 'analyzed', variant: 'informative' },
  compared: { label: 'compared', variant: 'positive' },
  saved: { label: 'saved', variant: 'informative' },
}

const ENGINE_LABEL: Record<MonitorHealth['engine'], string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

/** The two setup steps, as a compact readout; the current one is emphasized. */
function SetupTrack({ connected }: { connected: boolean }) {
  const steps = [
    { label: 'Connect a database', done: connected },
    { label: 'Turn on monitoring', done: false },
  ]
  return (
    <ol
      aria-label="Setup"
      className="flex flex-wrap items-center gap-x-3 gap-y-2"
    >
      {steps.map((step, index) => {
        const current = !step.done && steps.slice(0, index).every((s) => s.done)
        return (
          <li key={step.label} className="flex items-center gap-2">
            {index > 0 ? (
              <span
                aria-hidden="true"
                className="h-px w-6 bg-border-layout-2"
              />
            ) : null}
            {step.done ? (
              <Icon
                name="tick"
                label="Done"
                className="h-4 w-4 text-content-positive-soft"
              />
            ) : (
              <span
                aria-hidden="true"
                className={
                  current
                    ? 'flex h-5 w-5 items-center justify-center rounded-full border border-border-primary-soft text-caption text-content-primary-soft'
                    : 'flex h-5 w-5 items-center justify-center rounded-full border border-border-layout-2 text-caption text-content-layout-3'
                }
              >
                {index + 1}
              </span>
            )}
            <Text
              level={current ? 'label-small' : 'caption'}
              className={
                current ? 'text-content-layout-1' : 'text-content-layout-3'
              }
            >
              {step.label}
            </Text>
          </li>
        )
      })}
    </ol>
  )
}

export function FirstRunHome() {
  const navigate = useNavigate()
  return (
    <div className="grid grid-cols-1 gap-4 desktop:grid-cols-12">
      <Card className="desktop:col-span-7">
        <Card.Header className="gap-4">
          <SetupTrack connected={false} />
          <div className="space-y-1">
            <Card.Title>See what your database spends its time on</Card.Title>
            <Card.Description>
              Connect PostgreSQL or MySQL with a read-only user, then turn on
              monitoring. RDST reads query statistics and never touches your
              application traffic.
            </Card.Description>
          </div>
        </Card.Header>
        <Card.Footer className="flex-wrap justify-start gap-x-4 gap-y-2">
          <Button
            variant="primary"
            modifier="solid"
            label="Connect a database"
            icon="arrow-right"
            iconPosition="right"
            onClick={() => navigate({ to: '/onboarding' })}
          />
          <Text level="caption" className="text-content-layout-3">
            RDST finds your tables on its own.
          </Text>
        </Card.Footer>
      </Card>
      <div className="desktop:col-span-5">
        <DemoCard compact />
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: string
  unit?: string
  tone?: 'warning'
}) {
  return (
    <div className="min-w-0 space-y-0.5">
      <Text as="p" level="caption" className="text-content-layout-3">
        {label}
      </Text>
      <p className="flex items-baseline gap-1">
        <Text
          as="span"
          level="headline-3"
          className={
            tone === 'warning'
              ? 'tabular-nums text-content-warning-soft'
              : 'tabular-nums text-content-layout-1'
          }
        >
          {value}
        </Text>
        {unit ? (
          <Text as="span" level="caption" className="text-content-layout-3">
            {unit}
          </Text>
        ) : null}
      </p>
    </div>
  )
}

/**
 * The one card Home leads with: the connected database, in whatever state its
 * monitoring is in, and the way into the Database page.
 */
export function DatabaseCard({
  state,
  target,
  health,
  monitorQueries,
  pulse,
  monitoringOn,
  attentionCount,
}: {
  state: Exclude<HomeState, 'first-run'>
  target: string
  health: MonitorHealth | undefined
  monitorQueries: MonitorQueryPage | undefined
  pulse: MonitorPulseState
  monitoringOn: boolean
  /** Queries with an open Inbox item (Needs attention and Watch). */
  attentionCount?: number | null
}) {
  const navigate = useNavigate()
  const openDatabase = () => navigate({ to: '/database' })
  const engine = health ? ENGINE_LABEL[health.engine] : null
  const history = historyLine(health?.history)
  const subtitle =
    state === 'connected'
      ? 'Monitoring is off'
      : state === 'collecting'
        ? 'Collecting the first readings'
        : monitoringOn
          ? history
          : `Monitoring paused${history ? ` · ${history}` : ''}`

  let body: ReactNode
  let action: { label: string; solid: boolean }
  if (state === 'connected') {
    action = { label: 'Turn on monitoring', solid: true }
    body = (
      <div className="space-y-4">
        <SetupTrack connected />
        <Text
          as="p"
          level="body-small"
          className="max-w-2xl text-content-layout-2"
        >
          RDST reads {target}'s query statistics every minute, spots new and
          slower queries, and writes a nightly report. It only reads.
        </Text>
      </div>
    )
  } else if (state === 'collecting') {
    const model = firstRun({
      now: pulse.now,
      history: health?.history,
      pulses: pulse.pulses,
      settings: undefined,
      queries: null,
      queue: null,
    })
    const span = health?.history?.span_seconds ?? 0
    const progress = Math.min(1, span / (CHART_READY_MINUTES * 60))
    action = { label: 'Watch it fill in', solid: true }
    body = (
      <div className="max-w-2xl space-y-3">
        <Text as="p" level="body-small" className="text-content-layout-2">
          {model.firstAt == null
            ? 'The first reading is on its way.'
            : `${model.readings} ${model.readings === 1 ? 'reading' : 'readings'} so far. The chart is drawing as readings come in.`}
        </Text>
        <Progress
          value={Math.round(progress * 100)}
          max={100}
          label="The chart's first five minutes"
          className="h-1.5"
        />
      </div>
    )
  } else {
    const latency = formatMsWithUnit(health?.now.mean_ms)
    const attention = attentionCount ?? null
    action = { label: 'Open database', solid: true }
    body = (
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-4 tablet:grid-cols-4">
          <Stat
            label="Throughput"
            value={formatQps(health?.now.qps)}
            unit="queries/s"
          />
          <Stat label="Avg latency" value={latency.value} unit={latency.unit} />
          <Stat
            label="Queries tracked"
            value={monitorQueries ? String(monitorQueries.total) : '-'}
          />
          <Stat
            label="Need attention"
            value={attention == null ? '-' : String(attention)}
            tone={attention ? 'warning' : undefined}
          />
        </div>
        <Show when={health && health.series.length > 0 ? health : null}>
          {(value) => (
            <div className="space-y-1">
              <Text as="p" level="caption" className="text-content-layout-3">
                Queries per second, last hour
              </Text>
              <MiniTrend
                ariaLabel="Queries per second, last hour"
                color={SERIES_COLOR.throughput}
                points={value.series.map((point) => ({
                  x: point.ts,
                  y: point.qps,
                }))}
                formatValue={(qps) => `${formatQps(qps)} queries/s`}
                height={56}
              />
            </div>
          )}
        </Show>
      </div>
    )
  }

  return (
    <Card data-testid="home-database-card">
      <Card.Header className="flex-row items-center gap-4">
        <DatabaseGlyph
          size={56}
          tone={state === 'connected' || !monitoringOn ? 'neutral' : 'positive'}
          qps={state === 'active' && monitoringOn ? health?.now.qps : null}
          beat={monitoringOn ? pulse.beat : undefined}
        />
        <div className="min-w-0 flex-1 space-y-0.5">
          <Card.Title className="truncate">{target}</Card.Title>
          <Text
            as="p"
            level="caption"
            className="truncate text-content-layout-3"
          >
            {[engine, subtitle].filter(Boolean).join(' · ')}
          </Text>
        </div>
        <div className="max-tablet:hidden">
          <Button
            variant="primary"
            modifier={action.solid ? 'solid' : 'outline'}
            label={action.label}
            icon="arrow-right"
            iconPosition="right"
            onClick={openDatabase}
          />
        </div>
      </Card.Header>
      <Card.Content>{body}</Card.Content>
      <Card.Footer className="tablet:hidden">
        <Button
          variant="primary"
          modifier={action.solid ? 'solid' : 'outline'}
          label={action.label}
          icon="arrow-right"
          iconPosition="right"
          fullWidth
          onClick={openDatabase}
        />
      </Card.Footer>
    </Card>
  )
}

export function ContinueCard({
  target,
  recents,
}: {
  target: string
  recents: ContinueItem[]
}) {
  const navigate = useNavigate()
  return (
    <Card>
      <Card.Header>
        <Card.Title>Continue where you left off</Card.Title>
        <Card.Description>
          Pick up the query you were last working on.
        </Card.Description>
      </Card.Header>
      <Card.Content className="space-y-2">
        <For each={recents} keyExtractor={(item) => item.hash}>
          {(item) => (
            <InteractiveRow
              label={`Continue with ${item.nextAction.toLowerCase()} for ${item.label}`}
              onClick={() => {
                if (item.nextAction === 'Analyze') {
                  navigate({
                    to: '/results',
                    search: {
                      query: fillCapturedParams(
                        item.sql,
                        item.mostRecentParams
                      ),
                      target,
                      origin: 'home',
                    },
                  })
                  return
                }
                if (item.nextAction === 'Review') {
                  // The analysis this query already has, opened on its Analyze
                  // tab. Recall never re-runs (A6).
                  navigate({
                    to: '/database',
                    search: { target, query: item.hash, tab: 'analysis' },
                  })
                  return
                }
                navigate({
                  to: '/database',
                  search: { target, query: item.hash, tab: 'readyset' },
                })
              }}
              className="flex items-center gap-3 rounded-xl bg-surface-layout-2/50 px-3 py-3 text-left transition-colors hover:bg-surface-raised"
            >
              <Tag
                size="small"
                variant={CONTINUE_TAG[item.kind].variant}
                modifier="ghost"
                label={CONTINUE_TAG[item.kind].label}
              />
              <Text
                level="mono-small"
                className="min-w-0 flex-1 truncate text-content-layout-2"
              >
                {item.label}
              </Text>
              <Text level="caption" className="text-content-primary-soft">
                {item.nextAction} →
              </Text>
            </InteractiveRow>
          )}
        </For>
      </Card.Content>
    </Card>
  )
}

/** Home once a database is connected: the database first, everything else after. */
export function DatabaseHome({
  state,
  target,
  health,
  monitorQueries,
  pulse,
  monitoringOn,
  recents,
  counts,
  historyDays,
  attentionCount,
}: {
  attentionCount?: number | null
  state: Exclude<HomeState, 'first-run'>
  target: string
  health: MonitorHealth | undefined
  monitorQueries: MonitorQueryPage | undefined
  pulse: MonitorPulseState
  monitoringOn: boolean
  recents: ContinueItem[]
  counts: PortfolioCounts
  historyDays: number | null
}) {
  const sustainedUse =
    historyDays !== null && historyDays >= 30 && counts.compared > 0
  return (
    <div className="space-y-4">
      <DemoBanner />
      <DatabaseCard
        state={state}
        target={target}
        health={health}
        monitorQueries={monitorQueries}
        pulse={pulse}
        monitoringOn={monitoringOn}
        attentionCount={attentionCount}
      />
      <Show when={recents.length > 0}>
        <ContinueCard target={target} recents={recents} />
      </Show>
      <JobCard
        to="/ask"
        icon="sparkles"
        title="Ask about your data"
        description={`Ask a question in plain words and get SQL that runs on ${target}.`}
      />
      <Show when={sustainedUse}>
        <HandRaiser
          signal="retention_30d"
          tone="accent"
          showDismiss
          message={`A month of RDST on ${target}, with ${counts.compared} Readyset comparison${counts.compared === 1 ? '' : 's'} retained. If this is heading to production, we'd like to help you size it.`}
        />
      </Show>
    </div>
  )
}
