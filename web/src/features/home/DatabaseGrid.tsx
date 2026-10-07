import { cn } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Icon } from '@rs/ui-new/icon'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQueries } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  type TargetMonitorLine,
  TargetMonitorStatus,
  useTargetMonitorLines,
} from '../../components/TargetMonitorStatus'
import { useTarget } from '../../hooks/useTarget'
import { actionKeys, fetchActions } from '../../lib/monitor/actions'
import {
  useInbox,
  useMonitorHealth,
  useMuteRules,
} from '../../lib/monitor/hooks'
import type { MonitorHealth } from '../../lib/monitor/types'
import { DatabaseGlyph } from '../monitor-kit/DatabaseGlyph'
import {
  GLOW_LABEL,
  type HealthGlow,
  healthGlow,
  inboxStanding,
} from './homeModel'

const ENGINE_LABEL: Record<MonitorHealth['engine'], string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

const GLOW_CARD: Record<HealthGlow, string> = {
  positive: 'border-border-positive-soft shadow-glow-positive',
  warning: 'border-border-warning-soft shadow-glow-warning',
  negative: 'border-border-negative-soft shadow-glow-negative',
  neutral: 'border-border-layout-1',
}

interface DatabaseTileProps {
  target: string
  monitored: boolean
  line: TargetMonitorLine | null | undefined
  openActions: number | null
  urgent: number
  watch: number
  fresh: number
}

function Count({
  value,
  label,
  tone,
}: {
  value: number | null
  label: string
  tone?: 'negative' | 'warning'
}) {
  return (
    <div className="min-w-0">
      <Text
        as="p"
        level="headline-4"
        className={cn(
          'tabular-nums',
          tone === 'negative'
            ? 'text-content-negative-soft'
            : tone === 'warning'
              ? 'text-content-warning-soft'
              : value
                ? 'text-content-layout-1'
                : 'text-content-layout-3'
        )}
      >
        {value ?? '-'}
      </Text>
      <Text as="p" level="caption" className="text-content-layout-3">
        {label}
      </Text>
    </div>
  )
}

function DatabaseTile({
  target,
  monitored,
  line,
  openActions,
  urgent,
  watch,
  fresh,
}: DatabaseTileProps) {
  const health = useMonitorHealth(monitored ? target : '', '1h')
  const glow = healthGlow({
    monitored,
    failing: line?.tone === 'negative',
    urgent,
    watch,
    openActions: openActions ?? 0,
  })
  const engine = health.data ? ENGINE_LABEL[health.data.engine] : null
  return (
    <Link
      to="/database"
      search={{ target }}
      data-testid="home-database-card"
      data-glow={glow}
      aria-label={`${target}, ${GLOW_LABEL[glow]}`}
      className={cn(
        'group flex min-w-0 flex-col gap-5 rounded-2xl border bg-surface-layout-1 p-5 transition-colors hover:bg-surface-layout-2',
        'outline-none focus-visible:shadow-focus',
        GLOW_CARD[glow]
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <DatabaseGlyph
          size={44}
          tone={glow}
          qps={monitored ? health.data?.now.qps : null}
          noTooltip
        />
        <div className="min-w-0 flex-1">
          <Text
            as="p"
            level="label-large"
            className="truncate text-content-layout-1"
          >
            {target}
          </Text>
          <Text
            as="p"
            level="caption"
            className="truncate text-content-layout-3"
          >
            {engine ?? 'Database'}
          </Text>
        </div>
        <Tag
          size="small"
          variant={glow}
          modifier="ghost"
          label={GLOW_LABEL[glow]}
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Count
          value={monitored ? openActions : null}
          label={openActions === 1 ? 'Open action' : 'Open actions'}
          tone={openActions ? 'warning' : undefined}
        />
        <Count
          value={monitored ? fresh : null}
          label="New in Inbox"
          tone={urgent ? 'negative' : undefined}
        />
      </div>
      {line?.tone === 'negative' && line.reason ? (
        <p
          className="text-body-small text-content-layout-2"
          data-testid="database-card-reason"
        >
          {line.reason}
        </p>
      ) : null}
      <div className="mt-auto flex items-center justify-between gap-3 border-t border-border-layout-1 pt-3">
        <TargetMonitorStatus line={line} compact className="min-w-0" />
        <Icon
          name="arrow-right"
          label=""
          aria-hidden="true"
          className="h-4 w-4 shrink-0 text-content-layout-3 transition-transform group-hover:translate-x-0.5"
        />
      </div>
    </Link>
  )
}

/** Every database at a glance: how it's doing and what's open on it. */
export function DatabaseGrid({
  targets,
  monitored,
}: {
  targets: string[]
  monitored: string[]
}) {
  // Monitored databases lead; the rest keep the configured order.
  const ordered = [
    ...targets.filter((target) => monitored.includes(target)),
    ...targets.filter((target) => !monitored.includes(target)),
  ]
  const lines = useTargetMonitorLines(ordered, true)
  const inbox = useInbox({ state: 'open' })
  const rules = useMuteRules()
  const actions = useQueries({
    queries: ordered.map((target) => ({
      queryKey: actionKeys.actions(target),
      queryFn: () => fetchActions(target),
      enabled: monitored.includes(target),
      staleTime: 60_000,
    })),
  })
  return (
    <div className="grid grid-cols-1 gap-4 tablet:grid-cols-[repeat(auto-fit,minmax(18rem,1fr))]">
      {ordered.map((target, index) => (
        <DatabaseTile
          key={target}
          target={target}
          monitored={monitored.includes(target)}
          line={lines.get(target)}
          openActions={actions[index]?.data?.change_count ?? null}
          {...inboxStanding(inbox.data?.items ?? [], rules.data?.rules, target)}
        />
      ))}
    </div>
  )
}

interface NextStep {
  label: string
  icon: IconStrokeName
  to: string
  search?: Record<string, string>
}

/** A short row of things to do next, on the selected database. */
export function NextSteps() {
  const { target } = useTarget()
  const scoped: Record<string, string> = target ? { target } : {}
  const steps: NextStep[] = [
    { label: 'Ask about your data', icon: 'sparkles', to: '/ask' },
    {
      label: 'Analyze a query',
      icon: 'search',
      to: '/database',
      search: { ...scoped, panel: 'analyze' },
    },
  ]
  return (
    <section aria-label="Things you can do" className="space-y-3">
      <Text as="h2" level="label-medium" className="text-content-layout-2">
        Things you can do
      </Text>
      <div className="grid grid-cols-1 gap-3 tablet:grid-cols-2">
        {steps.map((step) => (
          <Link
            key={step.label}
            to={step.to}
            search={step.search as never}
            className="group flex items-center gap-3 rounded-xl border border-border-layout-1 bg-surface-layout-1 px-4 py-3 transition-colors hover:bg-surface-layout-2 outline-none focus-visible:shadow-focus"
          >
            <Icon
              name={step.icon}
              label=""
              aria-hidden="true"
              className="h-4 w-4 shrink-0 text-content-layout-2"
            />
            <Text level="label-medium" className="flex-1 text-content-layout-1">
              {step.label}
            </Text>
            <Icon
              name="arrow-right"
              label=""
              aria-hidden="true"
              className="h-4 w-4 shrink-0 text-content-layout-3 transition-transform group-hover:translate-x-0.5"
            />
          </Link>
        ))}
      </div>
    </section>
  )
}
