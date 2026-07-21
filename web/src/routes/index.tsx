import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Button } from '@rs/ui-new/button'
import { For } from '@rs/ui-new/for'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { HandRaiser } from '../components/HandRaiser'
import { useTarget } from '../hooks/useTarget'
import { fetchQueryRegistry, fetchSchemaStatus } from '../lib/api'
import { formatTimestamp } from '../lib/formatters'
import {
  type ContinueItem,
  continueItems,
  deriveHomeState,
  type PortfolioCounts,
  portfolioCounts,
} from '../lib/homeState'
import { fillCapturedParams } from '../lib/sqlParameters'
import { useTrialSource } from '../lib/trialQueries'
import { fetchAuditRuns } from '../lib/useAudit'
import { cachedRegistryHashes, fetchCacheList } from '../lib/useCache'
import { useSystemStatus } from '../lib/useSystemStatus'

export const Route = createFileRoute('/')({
  component: HomePage,
})

interface JobCardProps {
  to: string
  icon: IconStrokeName
  title: string
  description: string
  chip?: {
    label: string
    variant: 'positive' | 'warning' | 'informative' | 'primary' | 'neutral'
    modifier?: 'solid' | 'outline' | 'ghost'
  }
  /** The single "start here" action: full-width, raised (elevation-1), one
   *  primary accent — the page's visual anchor. */
  featured?: boolean
}

function JobCard({
  to,
  icon,
  title,
  description,
  chip,
  featured,
}: JobCardProps) {
  if (featured) {
    return (
      <Link
        to={to}
        className="group block rounded-2xl border border-border-primary-soft bg-surface-raised p-6 shadow-elevation-1 transition-shadow hover:shadow-elevation-2"
      >
        <VStack className="gap-3 items-start">
          <HStack className="gap-3 items-center w-full">
            <div className="w-11 h-11 rounded-xl bg-surface-primary-soft flex items-center justify-center shrink-0">
              <Icon
                name={icon}
                label=""
                className="w-6 h-6 text-content-primary-soft"
              />
            </div>
            <Text level="headline-5" className="text-content-layout-1 flex-1">
              {title}
            </Text>
            <Show when={chip}>
              {(c) => (
                <Tag
                  size="small"
                  variant={c.variant}
                  modifier={c.modifier ?? 'ghost'}
                  label={c.label}
                />
              )}
            </Show>
          </HStack>
          <HStack className="gap-3 items-center w-full">
            <Text level="body-medium" className="text-content-layout-2 flex-1">
              {description}
            </Text>
            <Icon
              name="arrow-right"
              label=""
              className="w-5 h-5 text-content-primary-soft shrink-0 transition-transform group-hover:translate-x-0.5"
            />
          </HStack>
        </VStack>
      </Link>
    )
  }
  return (
    <Link
      to={to}
      className="group rounded-xl bg-surface-layout-1 p-5 transition-all hover:bg-surface-raised hover:shadow-elevation-1"
    >
      <VStack className="gap-3 items-start h-full">
        <HStack className="gap-3 items-center w-full">
          <div className="w-10 h-10 rounded-xl bg-surface-primary-soft/40 flex items-center justify-center shrink-0">
            <Icon
              name={icon}
              label=""
              className="w-5 h-5 text-content-primary-soft"
            />
          </div>
          <Text level="label-large" className="text-content-layout-1 flex-1">
            {title}
          </Text>
          <Icon
            name="arrow-right"
            label=""
            className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
          />
        </HStack>
        <Text level="body-small" className="text-content-layout-3">
          {description}
        </Text>
        <Show when={chip}>
          {(c) => (
            <Tag
              size="small"
              variant={c.variant}
              modifier={c.modifier ?? 'ghost'}
              label={c.label}
            />
          )}
        </Show>
      </VStack>
    </Link>
  )
}

// ---------------------------------------------------------------------------
// State 1 — zero targets. One hero, one verb; the demo is the ungated
// no-commitment path. Spec: design/proposals/home-1-first-run.html
// ---------------------------------------------------------------------------

export function FirstRunHome({ needsApiKey }: { needsApiKey: boolean }) {
  const navigate = useNavigate()
  return (
    <div className="mx-auto pt-6">
      <Link
        to="/demo"
        className="group flex items-start gap-3 rounded-2xl border border-border-rising-soft bg-surface-rising-soft p-5 shadow-elevation-1 transition-shadow hover:shadow-elevation-2 mb-4"
      >
        <div className="w-10 h-10 rounded-xl bg-surface-rising-solid/40 flex items-center justify-center shrink-0">
          <Icon
            name="layers"
            label=""
            className="w-5 h-5 text-content-primary-soft transition-[scale] group-hover:scale-110"
          />
        </div>

        <div className="flex gap-2 justify-between w-full">
          <div className="flex flex-col gap-0 justify-start items-start flex-1">
            <Text level="label-medium" className="text-content-layout-1 flex-1">
              See Readyset Platform in action — no setup, no sign-up
            </Text>
            <Text
              level="body-small"
              className="text-content-layout-2 flex-1 max-w-3xl"
            >
              See QueryPilot cache your hottest queries in real time as Readyset
              and Postgres run the same workload side by side. Local containers,
              one-click cleanup.
            </Text>
          </div>
          <Tag
            size="base"
            fullWidth={false}
            variant="rising"
            modifier="solid"
            label="Try it"
          />
        </div>
      </Link>
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4 mb-4">
        <div className="relative rounded-2xl bg-surface-raised p-6 shadow-elevation-1">
          <span className="absolute top-4 right-4 w-6 h-6 rounded-lg bg-surface-layout-1 text-content-layout-3 flex items-center justify-center text-body-small">
            1
          </span>
          <div className="w-10 h-10 rounded-xl bg-surface-layout-1/50 flex items-center justify-center mb-3">
            <Icon
              name="database"
              label=""
              className="w-5 h-5 text-content-primary-soft"
            />
          </div>
          <Text level="label-large" className="text-content-layout-1">
            Connect a database
          </Text>
          <Text
            level="body-small"
            className="text-content-layout-2 mt-1 mb-4 min-h-14"
          >
            PostgreSQL or MySQL. The wizard connects and validates in about a
            minute. Read-only by default; nothing leaves your machine.
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Connect a database"
            icon="arrow-right"
            iconPosition="right"
            onClick={() => navigate({ to: '/onboarding' })}
          />
        </div>
        <div className="relative rounded-2xl bg-surface-raised p-6 shadow-elevation-1">
          <span className="absolute top-4 right-4 w-6 h-6 rounded-lg bg-surface-layout-1 text-content-layout-3 flex items-center justify-center text-body-small">
            2
          </span>
          <div className="w-10 h-10 rounded-xl bg-surface-layout-1/50 flex items-center justify-center mb-3">
            <Icon
              name="sparkles"
              label=""
              className="w-5 h-5 text-content-primary-soft"
            />
          </div>
          <Text level="label-large" className="text-content-layout-1">
            Add your Anthropic key
          </Text>
          <Text
            level="body-small"
            className="text-content-layout-2 mt-1 mb-4 min-h-14"
          >
            Powers schema discovery, Ask, and analysis advice. No key yet? Start
            with a trial — it works the same and you can swap the key in later.
          </Text>
          <HStack className="gap-2">
            <Button
              variant="primary"
              modifier="outline"
              label={needsApiKey ? 'Add key' : 'Key configured'}
              onClick={() => navigate({ to: '/configure' })}
            />
            <Button
              variant="primary"
              modifier="ghost"
              label="Start trial"
              onClick={() => navigate({ to: '/configure' })}
            />
          </HStack>
        </div>
      </div>
      <div className="rounded-2xl border border-border-primary-soft/10 bg-surface-layout-1 p-4">
        <Text level="body-small" className="text-content-layout-2">
          With both in place, the next screen offers one thing:{' '}
          <span className="text-content-layout-1">schema discovery</span> — RDST
          profiles your tables and writes the context that turns generic AI into
          an assistant that knows your column shapes, conventions, and business
          terms.
        </Text>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// State 2 — connected, semantic layer not yet discovered. Discovery is the
// hero; everything else is a "meanwhile". The design card's live preview is
// deliberately NOT reproduced here — showing fake profiling rows would break
// the honesty contract; the product shows a plain "what you get" list until
// discovery streams real progress. Spec: design/proposals/home-2-connected.html
// ---------------------------------------------------------------------------

export function ConnectedHome({
  target,
  needsApiKey,
}: {
  target: string
  needsApiKey: boolean
}) {
  const navigate = useNavigate()
  return (
    <div className="space-y-4 w-full">
      <VStack className="gap-1 items-start">
        <Text level="headline-5" className="text-content-layout-1">
          {target} is connected — now teach RDST what it means
        </Text>
        <Text level="body-small" className="text-content-layout-2">
          One step stands between you and AI that actually knows this database.
        </Text>
      </VStack>

      <div className="rounded-2xl border border-border-primary-soft bg-surface-primary-soft/30 p-6">
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-6">
          <VStack className="gap-3 items-start">
            <Text level="headline-5" className="text-content-layout-1">
              Discover your schema
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              RDST profiles every table — column shapes, null rates, sample
              values, row counts — and writes descriptions and business context
              for each one. This is the difference between AI that guesses at
              your data and AI that asks the right questions back.
            </Text>
            <Show
              when={!needsApiKey}
              fallback={
                <HStack className="gap-2 items-center">
                  <Tag
                    size="small"
                    variant="warning"
                    label="Needs an Anthropic key"
                  />
                  <Button
                    variant="primary"
                    modifier="outline"
                    size="small"
                    label="Add key"
                    onClick={() => navigate({ to: '/configure' })}
                  />
                </HStack>
              }
            >
              <Button
                variant="primary"
                modifier="solid"
                label="Discover schema"
                icon="arrow-right"
                iconPosition="right"
                onClick={() => navigate({ to: '/schema' })}
              />
            </Show>
            <Text level="caption" className="text-content-layout-3">
              Runs in the background · read-only queries · uses your Anthropic
              key
            </Text>
          </VStack>
          <VStack className="gap-2 items-start">
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              What discovery produces
            </Text>
            <VStack className="gap-2 items-stretch w-full">
              <div className="rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3">
                <Text level="body-small" className="text-content-layout-2">
                  A description and business context for every table
                </Text>
              </div>
              <div className="rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3">
                <Text level="body-small" className="text-content-layout-2">
                  Column shapes: types, ranges, null rates, sample values
                </Text>
              </div>
              <div className="rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3">
                <Text level="body-small" className="text-content-layout-2">
                  Sharper Ask: clarifying questions instead of guessed tables
                </Text>
              </div>
            </VStack>
          </VStack>
        </div>
      </div>

      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <JobCard
          to="/audit"
          icon="document-validation"
          title="Meanwhile: run a health check"
          description="Doesn't need the schema — sizing, slow spots, and cache candidates while discovery runs."
        />
        <JobCard
          to="/demo"
          icon="querypilot"
          title="Meanwhile: see the demo"
          description="Sandboxed Readyset side-by-side on a sample database. Zero impact on your target, no sign-up."
        />
        <JobCard
          to="/ask"
          icon="sparkles"
          title="Can't wait? Ask now"
          description="Works today via live introspection — answers get sharper the moment discovery lands."
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// State 3 — active use. Portfolio, not pipeline: every tile is a door, the
// highlight is data-driven. Spec: design/proposals/home-3-active.html
// ---------------------------------------------------------------------------

function PortfolioTile({
  label,
  value,
  unit,
  action,
  to,
  highlight,
}: {
  label: string
  value: string
  unit: string
  action: string
  to: string
  highlight?: boolean
}) {
  return (
    <Link
      to={to}
      className={
        highlight
          ? 'rounded-xl border border-border-primary-soft bg-surface-primary-soft/40 p-4 hover:bg-surface-primary-soft/60 transition-colors'
          : 'rounded-xl bg-surface-layout-1 p-4 transition-all hover:bg-surface-raised hover:shadow-elevation-1'
      }
    >
      <VStack className="gap-0.5 items-start">
        <Text level="caption" className="text-content-layout-3">
          {label}
        </Text>
        <Text level="headline-5" className="text-content-layout-1 tabular-nums">
          {value}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {unit}
        </Text>
        <Text level="caption" className="text-content-primary-soft mt-2">
          {action} →
        </Text>
      </VStack>
    </Link>
  )
}

const CONTINUE_TAG: Record<
  ContinueItem['kind'],
  { label: string; variant: 'primary' | 'informative' | 'positive' }
> = {
  asked: { label: 'asked', variant: 'primary' },
  analyzed: { label: 'analyzed', variant: 'informative' },
  cached: { label: 'cached', variant: 'positive' },
  saved: { label: 'saved', variant: 'informative' },
}

function ActiveHome({
  target,
  counts,
  recents,
  lastAuditLabel,
  retentionDays,
}: {
  target: string
  counts: PortfolioCounts
  recents: ContinueItem[]
  lastAuditLabel: string | null
  retentionDays: number | null
}) {
  const navigate = useNavigate()
  const gapOnCache = counts.candidates > 0
  const sustainedUse =
    retentionDays !== null && retentionDays >= 30 && counts.cached > 0
  return (
    <div className="space-y-4 w-full">
      <HStack className="gap-3 items-baseline">
        <Text level="headline-5" className="text-content-layout-1">
          Where your queries stand
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {target}
        </Text>
      </HStack>

      <div className="rounded-2xl border border-border-layout-1 bg-surface-layout-1 p-4">
        <Text
          level="overline"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          Query portfolio
        </Text>
        <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3 mt-3">
          <PortfolioTile
            label="Asked"
            value={String(counts.asked)}
            unit="questions saved"
            action="Ask another"
            to="/ask"
          />
          <PortfolioTile
            label="Analyzed"
            value={String(counts.analyzed)}
            unit="with execution plans"
            action="Analyze a query"
            to="/analyze"
          />
          <PortfolioTile
            label="Cached"
            value={String(counts.cached)}
            unit={
              counts.candidates > 0
                ? `${counts.candidates} candidate${counts.candidates === 1 ? '' : 's'} waiting`
                : 'serving from Readyset'
            }
            action={gapOnCache ? 'Cache them' : 'Manage caches'}
            to="/cache"
            highlight={gapOnCache}
          />
          <PortfolioTile
            label="Benchmarked"
            value="—"
            unit="not tracked yet"
            action="Run one"
            to="/benchmark"
          />
        </div>
        <Text level="caption" className="text-content-layout-3 mt-3">
          Every tile is a door, not a step — the highlight marks the biggest
          unclaimed win.
        </Text>
        <Show when={sustainedUse}>
          <div className="mt-4">
            <HandRaiser
              signal="retention_30d"
              tone="accent"
              showDismiss
              message={`A month of RDST on ${target}, ${counts.cached} cache${counts.cached === 1 ? '' : 's'} serving. If this is heading to production, we'd like to help you size it.`}
            />
          </div>
        </Show>
      </div>

      <Show when={recents.length > 0}>
        <div className="rounded-2xl border border-border-layout-1 bg-surface-layout-1 p-4">
          <Text
            level="overline"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            Continue where you left off
          </Text>
          <VStack className="gap-2 items-stretch mt-3">
            <For each={recents} keyExtractor={(item) => item.hash}>
              {(item) => (
                <button
                  type="button"
                  key={item.hash}
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
                        },
                      })
                    } else if (item.nextAction === 'Cache') {
                      navigate({ to: '/cache' })
                    } else {
                      navigate({ to: '/benchmark' })
                    }
                  }}
                  className="flex items-center gap-3 rounded-lg border border-border-layout-1 bg-surface-layout-2/40 px-3 py-2.5 hover:border-border-layout-2 transition-colors text-left"
                >
                  <Tag
                    size="small"
                    variant={CONTINUE_TAG[item.kind].variant}
                    modifier="ghost"
                    label={CONTINUE_TAG[item.kind].label}
                  />
                  <Text
                    level="mono-small"
                    className="text-content-layout-2 flex-1 truncate min-w-0"
                  >
                    {item.label}
                  </Text>
                  <Text
                    level="caption"
                    className="text-content-primary-soft shrink-0"
                  >
                    {item.nextAction} →
                  </Text>
                </button>
              )}
            </For>
          </VStack>
        </div>
      </Show>

      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <JobCard
          to="/audit"
          icon="document-validation"
          title="Health check"
          description="Sizing, slow spots, and cache opportunities."
          chip={
            lastAuditLabel
              ? { label: `Last run ${lastAuditLabel}`, variant: 'informative' }
              : { label: 'Never run', variant: 'primary' }
          }
        />
        <JobCard
          to="/top"
          icon="observe"
          title="Slow queries"
          description="See which queries are eating your database time right now."
          chip={{ label: `Against ${target}`, variant: 'informative' }}
        />
        <JobCard
          to="/demo"
          icon="querypilot"
          title="Demo"
          description="Sandboxed side-by-side — re-run anytime."
          chip={{ label: 'No sign-up', variant: 'positive' }}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// HomePage — derives the state and renders one of the three. The boundary
// rule lives in deriveHomeState (src/lib/homeState.ts), not here.
// ---------------------------------------------------------------------------

function HomePage() {
  const { target } = useTarget()
  const { data: status } = useSystemStatus()
  const { anthropicRequirement } = useTrialSource()

  const targetCount = status?.targets?.length ?? 0
  const hasTargets = targetCount > 0

  const { data: schemaStatus, isLoading: schemaLoading } = useQuery({
    queryKey: ['home', 'schema-status', target],
    queryFn: ({ signal }) => fetchSchemaStatus(target!, signal),
    staleTime: 60_000,
    enabled: hasTargets && !!target,
  })

  const { data: auditRuns } = useQuery({
    queryKey: ['home', 'audit-runs'],
    queryFn: () => fetchAuditRuns(),
    staleTime: 60_000,
    enabled: hasTargets,
  })

  const { data: registry } = useQuery({
    queryKey: ['home', 'registry'],
    queryFn: () => fetchQueryRegistry(),
    staleTime: 60_000,
    enabled: hasTargets,
  })

  // "Cached" reflects the live cache list for the selected target, shared with
  // the Caching page via this query key. The registry's readyset_query_id is
  // not a live signal -- it survives a DROP CACHE (rdst-e7s.32).
  const { data: cacheList } = useQuery({
    queryKey: ['cache-list', target],
    queryFn: () => fetchCacheList(target!),
    staleTime: 60_000,
    enabled: hasTargets && !!target,
  })

  const needsApiKey = anthropicRequirement
    ? !anthropicRequirement.satisfied
    : false
  const lastAudit = auditRuns?.runs?.[0]
  const entries = registry?.queries ?? []

  // Retention span for the sustained-use hand-raiser (rdst-dma.4): audit runs
  // are newest-first, so the oldest run's start dates first RDST activity.
  const oldestAudit = auditRuns?.runs?.[auditRuns.runs.length - 1]
  const retentionDays = oldestAudit
    ? Math.floor(
        (Date.now() - new Date(oldestAudit.started_at).getTime()) / 86_400_000
      )
    : null

  const cachedHashes = cachedRegistryHashes(cacheList)

  const homeState = deriveHomeState(targetCount, schemaStatus?.exists)
  const counts = portfolioCounts(entries, target ?? undefined, cachedHashes)
  const recents = continueItems(entries, target ?? undefined, cachedHashes)

  // Until the status fetch resolves, render nothing state-specific: a wrong
  // guess would flash the first-run hero at every returning user.
  if (!status || (hasTargets && schemaLoading)) {
    return <div className="w-full" />
  }

  return (
    <div className="space-y-8 w-full">
      <m.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon
              name="dashboard"
              label=""
              className="w-6 h-6 text-content-primary-soft"
            />
          </div>
          <VStack className="gap-0.5 items-start">
            <Text level="headline-4" className="text-content-layout-1">
              Welcome to RDST
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              Understand, diagnose, and speed up the queries running on your
              database.
            </Text>
          </VStack>
        </HStack>
      </m.div>

      <m.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.05 }}
      >
        <Show when={homeState === 'first-run'}>
          <FirstRunHome needsApiKey={needsApiKey} />
        </Show>
        <Show when={homeState === 'connected'}>
          <ConnectedHome target={target ?? ''} needsApiKey={needsApiKey} />
        </Show>
        <Show when={homeState === 'active'}>
          <ActiveHome
            target={target ?? ''}
            counts={counts}
            recents={recents}
            lastAuditLabel={
              lastAudit ? formatTimestamp(lastAudit.started_at) : null
            }
            retentionDays={retentionDays}
          />
        </Show>
      </m.div>

      <Text level="caption" className="text-content-layout-3">
        Looking for Agents, Guards, Fleet, or Benchmark? They live under
        Advanced in the sidebar. Databases, AI Settings, and Schema now live
        under Set up.
      </Text>
    </div>
  )
}
