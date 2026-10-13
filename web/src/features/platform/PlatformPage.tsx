import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { Verdict } from '@rs/ui-new/verdict'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { useOpenAddDatabase } from '../../components/configure/addDatabase'
import { TrialRegistrationDialog } from '../../components/TrialRegistrationDialog'
import {
  type ContainerProgress,
  type DemoFacts,
  type DiscoveryMode,
  getPreflight,
  type PreflightChecks,
  startLoad,
  useDemo,
  useDemoFacts,
} from '../../lib/useDemo'
import { ComparisonCard, type ComparisonMetric } from './ComparisonCard'
import { PatternList } from './PatternList'
import {
  canStart,
  DEMO_LIFETIME_S,
  dockerState,
  formatDownloadSize,
  formatRows,
  runningVerdict,
  shortClock,
  suggestedRows,
  type TryId,
  tryItems,
} from './platformModel'
import { ShutdownDialog } from './ShutdownDialog'
import { StartingCard } from './StartingCard'

const DOCKER_DESKTOP_URL = 'https://docs.docker.com/desktop/'
const IMAGES_MB = 1100
const ORDERS_ROWS = 1_500_000
const PG_VERSION = '16'
/** The workload's query count, shown before the demo has reported its patterns. */
const WORKLOAD_QUERIES = 44

const MODE_SWITCH_CONFIRM =
  'This resets all counters, drops every cache and starts the comparison over.'

type TourStepId =
  | 'chart'
  | 'manual'
  | 'manualResult'
  | 'querypilot'
  | 'qpWaiting'
  | 'autoCached'

const TRY_OF_STEP: Record<TourStepId, TryId> = {
  chart: 'compare',
  manual: 'cache',
  manualResult: 'cache',
  querypilot: 'querypilot',
  qpWaiting: 'querypilot',
  autoCached: 'querypilot',
}

// Tour completion lives on the machine (~/.rdst), not in the browser: a wiped
// RDST install replays the tour even in a browser that has seen it, and an
// existing install never auto-tours from a fresh browser profile.
async function fetchTourDone(): Promise<boolean> {
  try {
    const r = await fetch('/api/demo/tour')
    if (!r.ok) return true
    const data = (await r.json()) as { done?: boolean }
    return Boolean(data.done)
  } catch {
    return true
  }
}

function persistTourDone() {
  void fetch('/api/demo/tour-done', { method: 'POST' }).catch(() => {})
}

/** Post-tour invite: sign in to Readyset, or run RDST on your own database. */
function DemoTrialInvite() {
  const [dismissed, setDismissed] = useState(false)
  const [showTrial, setShowTrial] = useState(false)
  const openAddDatabase = useOpenAddDatabase()
  if (dismissed) return null
  return (
    <div
      role="dialog"
      aria-label="Try this on your own database"
      className="fixed right-4 bottom-4 z-50 w-full max-w-sm"
    >
      <div className="w-full rounded-xl border border-border-primary-soft bg-surface-raised p-5 shadow-elevation-2">
        <div className="flex items-start justify-between gap-3">
          <Text level="headline-4" className="text-content-layout-1">
            Try this on your own database
          </Text>
          <Pressable
            type="button"
            aria-label="Dismiss"
            onClick={() => setDismissed(true)}
            className="shrink-0 text-content-layout-3 hover:text-content-layout-1"
          >
            <Icon name="close" label="Dismiss" className="h-4 w-4" />
          </Pressable>
        </div>
        <Text level="body-small" className="mt-2 text-content-layout-2">
          Sign in for the included AI, then connect your database to test how
          much Readyset caching helps your queries.
        </Text>
        <div className="mt-4 flex flex-col gap-2">
          <Button
            variant="primary"
            modifier="solid"
            icon="sparkles"
            iconPosition="left"
            label="Sign in to Readyset"
            onClick={() => setShowTrial(true)}
          />
          <Button
            variant="primary"
            modifier="ghost"
            label="Connect a database"
            onClick={() => openAddDatabase()}
          />
        </div>
      </div>
      <TrialRegistrationDialog
        isOpen={showTrial}
        onClose={() => setShowTrial(false)}
        onSuccess={() => setShowTrial(false)}
      />
    </div>
  )
}

function FactsCard({
  checks,
  facts,
  queries,
}: {
  checks: PreflightChecks | null
  facts: DemoFacts | undefined
  queries: number
}) {
  const downloadMb =
    checks && !checks.images_present
      ? checks.download_mb
      : (facts?.download_mb ?? IMAGES_MB)
  const [downloadValue, downloadUnit] =
    formatDownloadSize(downloadMb).split(' ')
  const rows = formatRows(facts?.rows ?? ORDERS_ROWS)
  const engine = `PostgreSQL ${facts?.pg_version || PG_VERSION}`
  return (
    <section
      aria-labelledby="platform-facts-title"
      data-testid="platform-facts-card"
      className="mt-7 rounded-card border border-border-layout-2 bg-surface-primary-soft px-6 py-5.5"
    >
      <div className="flex items-baseline gap-3.5">
        <h2
          id="platform-facts-title"
          className="text-headline-5 text-content-layout-1"
        >
          What the demo builds
        </h2>
        <Text as="span" level="caption" className="text-content-layout-3">
          four local containers
        </Text>
      </div>
      <MetricRow bordered={false} className="mt-4">
        <Metric
          size="small"
          label="Orders database"
          value={rows.value}
          unit={rows.unit}
          detail={`rows, ${engine}, under live traffic`}
        />
        <Metric
          size="small"
          label="Readyset"
          value="3"
          detail="cache engine, QueryPilot router and accelerator"
        />
        <Metric
          size="small"
          label="Download"
          value={downloadValue}
          unit={downloadUnit}
          detail="once, kept for next time"
        />
        <Metric
          size="small"
          label="Queries"
          value={String(queries)}
          detail="on the orders schema"
        />
      </MetricRow>
    </section>
  )
}

function WhatToTry({
  items,
  enabled,
  activeId,
  done,
  onTry,
}: {
  items: ReturnType<typeof tryItems>
  /** The rows take a click once the demo is running, and wait at a lower tone until then. */
  enabled: boolean
  activeId: TryId | null
  done: Set<TryId>
  onTry: (id: TryId) => void
}) {
  return (
    <section aria-labelledby="platform-try" className="mt-12">
      <div className="mb-3 flex items-baseline gap-3.5">
        <h2 id="platform-try" className="text-headline-5 text-content-layout-1">
          What to try
        </h2>
        {!enabled && (
          <Text as="span" level="caption" className="text-content-layout-3">
            once the demo is running
          </Text>
        )}
      </div>
      <DividedList columns="1fr auto" className={cn(!enabled && 'opacity-45')}>
        {items.map((item) => (
          <ListRow key={item.id} asChild interactive>
            <Pressable
              data-testid={`try-${item.id}`}
              disabled={!enabled}
              aria-current={activeId === item.id ? 'step' : undefined}
              className="w-full text-left disabled:opacity-100"
              onClick={() => onTry(item.id)}
            >
              <span className="min-w-0">
                <Text
                  as="span"
                  level="body-medium"
                  className="block font-medium text-content-layout-1"
                >
                  {item.name}
                </Text>
                <Text
                  as="span"
                  level="caption"
                  className="mt-0.5 block text-content-layout-3"
                >
                  {item.before}
                  {item.mono && (
                    <span className="font-mono text-content-layout-2">
                      {item.mono}
                    </span>
                  )}
                  {item.after}
                </Text>
              </span>
              <Icon
                name={done.has(item.id) ? 'tick' : 'chevron-right'}
                label={done.has(item.id) ? 'done' : ''}
                size="small"
                className="justify-self-end pr-1.5 text-content-layout-3"
              />
            </Pressable>
          </ListRow>
        ))}
      </DividedList>
      <Text as="p" level="caption" className="mt-3.5 text-content-layout-3">
        Runs in four local containers. Nothing leaves your Mac; it removes
        itself after an hour, or sooner with Shut down demo.
      </Text>
    </section>
  )
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

export function PlatformPage() {
  const d = useDemo()
  const facts = useDemoFacts()
  const [checks, setChecks] = useState<PreflightChecks | null>(null)
  const [checking, setChecking] = useState(true)
  const recheck = useCallback(async () => {
    setChecking(true)
    try {
      const next = await getPreflight()
      setChecks(next)
      return next
    } catch {
      setChecks(null)
      return null
    } finally {
      setChecking(false)
    }
  }, [])
  useEffect(() => {
    if (d.phase === 'idle') void recheck()
  }, [d.phase, recheck])

  const [metric, setMetric] = useState<ComparisonMetric>('qps')
  const [confirmingShutdown, setConfirmingShutdown] = useState(false)
  const [confirmingStart, setConfirmingStart] = useState(false)
  const [pendingMode, setPendingMode] = useState<DiscoveryMode | null>(null)
  const [modeResetting, setModeResetting] = useState(false)
  const [startedAt, setStartedAt] = useState(() => Date.now())

  const [tourDone, setTourDone] = useState<boolean | null>(null)
  const [tourStep, setTourStep] = useState<TourStepId | null>(null)
  const [tried, setTried] = useState<Set<TryId>>(() => new Set())
  useEffect(() => {
    let cancelled = false
    void fetchTourDone().then((done) => {
      if (!cancelled) setTourDone(done)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const noticeRef = useRef<string | null | undefined>(undefined)
  const errorRef = useRef<string | null | undefined>(undefined)
  const healthRef = useRef<Record<string, ContainerProgress['state']>>({})
  useEffect(() => {
    if (d.error && d.error !== errorRef.current)
      toast({
        title: 'Something went wrong',
        description: d.error,
        variant: 'negative',
      })
    errorRef.current = d.error
  }, [d.error])
  useEffect(() => {
    if (d.notice && d.notice !== noticeRef.current)
      toast({ description: d.notice })
    noticeRef.current = d.notice
  }, [d.notice])
  useEffect(() => {
    for (const c of d.containers) {
      const prev = healthRef.current[c.name]
      if (prev !== c.state) {
        if (c.state === 'error')
          toast({
            title: 'A demo container failed',
            description: c.label,
            variant: 'negative',
          })
        else if (c.state === 'recovering')
          toast({
            title: 'Recovering a demo container',
            description: c.label,
            variant: 'warning',
          })
      }
      healthRef.current[c.name] = c.state
    }
  }, [d.containers])

  // The sixth step of a start: the workload opens the comparison on its own.
  const { phase, loadRunning, refreshStatus, refreshHistory, refreshPatterns } =
    d
  const startedLoadRef = useRef(false)
  useEffect(() => {
    if (phase !== 'ready') {
      startedLoadRef.current = false
      return
    }
    if (loadRunning || startedLoadRef.current) return
    startedLoadRef.current = true
    void startLoad()
      .then(() =>
        Promise.all([refreshStatus(), refreshHistory(), refreshPatterns()])
      )
      .catch(() => {})
  }, [phase, loadRunning, refreshStatus, refreshHistory, refreshPatterns])

  const suggestions = suggestedRows(d.patterns)
  const hasManualCache =
    d.patterns.some((row) => row.status === 'cached_manual') ||
    d.events.some((event) => event.type === 'manual_cache')
  const hasQueryPilotCache = d.patterns.some(
    (row) => row.status === 'cached_querypilot'
  )
  useEffect(() => {
    if (
      tourStep === 'manual' &&
      suggestions.length >= 2 &&
      suggestions.every((row) => row.status === 'cached_manual')
    )
      setTourStep('manualResult')
  }, [suggestions, tourStep])
  useEffect(() => {
    if (tourStep === 'querypilot' && d.querypilotOn)
      setTourStep(hasQueryPilotCache ? 'autoCached' : 'qpWaiting')
    if (tourStep === 'qpWaiting' && hasQueryPilotCache)
      setTourStep('autoCached')
  }, [d.querypilotOn, hasQueryPilotCache, tourStep])

  const done = new Set<TryId>()
  if (hasManualCache) done.add('cache')
  if (d.querypilotOn || d.events.some((event) => event.type === 'qp_on'))
    done.add('querypilot')
  if (tried.has('compare')) done.add('compare')
  const allTried = done.size === 3
  useEffect(() => {
    if (allTried && tourDone === false) {
      persistTourDone()
      setTourDone(true)
      setTourStep(null)
    }
  }, [allTried, tourDone])

  const start = useCallback(() => {
    setStartedAt(Date.now())
    void d.provision()
  }, [d])

  const onTry = useCallback(
    (id: TryId) => {
      setTried((current) => new Set(current).add(id))
      if (id === 'cache') {
        setTourStep('manual')
        scrollTo('platform-queries')
      } else if (id === 'querypilot') {
        setTourStep('querypilot')
        if (!d.querypilotOn) void d.toggleQueryPilot(true)
      } else {
        setTourStep('chart')
        scrollTo('platform-comparison')
      }
    },
    [d]
  )

  const onPolicy = useCallback(
    (next: DiscoveryMode) => {
      if (next === d.mode) return
      if (d.querypilotOn) {
        setPendingMode(next)
        return
      }
      void d.setDiscoveryMode(next)
    },
    [d]
  )
  const confirmMode = useCallback(async () => {
    const next = pendingMode
    setPendingMode(null)
    if (!next) return
    setModeResetting(true)
    try {
      await d.setDiscoveryMode(next)
    } finally {
      setModeResetting(false)
    }
  }, [d, pendingMode])

  const docker = dockerState(checks)
  const ready = d.phase === 'ready'
  const starting = d.phase === 'provisioning'
  const stopping = d.phase === 'tearing-down'
  const blocked =
    checks != null && docker === 'ok' && !canStart(checks)
      ? checks.amd64_emulation === 'unavailable'
        ? "Enable 'Use Rosetta for x86_64/amd64 emulation' in Docker settings, then try again."
        : `The demo needs about ${Math.round(checks.disk_required_gb || 2)} GB of free disk.`
      : null
  const dockerLine =
    docker === 'not_installed'
      ? "Docker isn't installed. Install Docker Desktop, then try again. The demo needs it for its four containers."
      : docker === 'not_running'
        ? "Docker isn't running. Start Docker Desktop, then try again. The demo needs it for its four containers."
        : null

  let meta: string
  let actions: ReactNode
  if (ready) {
    meta = d.autoTeardownAt
      ? `Running since ${shortClock(d.autoTeardownAt - DEMO_LIFETIME_S)}, removes itself at ${shortClock(d.autoTeardownAt)}`
      : 'Running'
    actions = (
      <>
        <Button
          variant="primary"
          modifier="ghost"
          label="Shut down demo"
          onClick={() => setConfirmingShutdown(true)}
        />
        {d.querypilotOn ? (
          <Button
            variant="primary"
            modifier="ghost"
            label="Turn off QueryPilot"
            disabled={modeResetting}
            onClick={() => void d.toggleQueryPilot(false)}
          />
        ) : (
          <Button
            variant="primary"
            modifier="solid"
            label="Turn on QueryPilot"
            disabled={modeResetting}
            onClick={() => onTry('querypilot')}
          />
        )}
      </>
    )
  } else if (starting) {
    meta = 'Starting, about 2 minutes'
    actions = (
      <Button
        variant="primary"
        modifier="ghost"
        label="Cancel"
        onClick={() => void d.tearDown()}
      />
    )
  } else if (stopping) {
    meta = 'Shutting down, about 10 seconds'
    actions = (
      <Button
        variant="primary"
        modifier="solid"
        label="Start the demo"
        disabled
      />
    )
  } else if (dockerLine || blocked) {
    meta =
      docker === 'not_installed'
        ? "Docker isn't installed"
        : docker === 'not_running'
          ? "Docker isn't running"
          : 'A caching demo on your Mac, about 2 minutes'
    actions = (
      <>
        <Button
          variant="primary"
          modifier="outline"
          label="Start the demo"
          disabled
        />
        <Button
          variant="primary"
          modifier="solid"
          label="Try again"
          loading={checking}
          onClick={() => void recheck()}
        />
      </>
    )
  } else {
    const requirements =
      checks == null
        ? ''
        : checks.images_present
          ? ' Docker is ready and the images are downloaded.'
          : ` Docker is ready. The first start downloads ${formatDownloadSize(checks.download_mb)}.`
    meta = `A caching demo on your Mac, about 2 minutes.${requirements}`
    actions = (
      <Button
        variant="primary"
        modifier="solid"
        label="Start the demo"
        disabled={!canStart(checks)}
        loading={checking}
        onClick={async () => {
          // Docker may have stopped since the page loaded.
          if (canStart(await recheck())) setConfirmingStart(true)
        }}
      />
    )
  }

  const activeTry = tourStep ? TRY_OF_STEP[tourStep] : null
  const verdict = ready
    ? runningVerdict(d.patterns, d.querypilotOn, d.mode)
    : {
        lead: 'Readyset caches your queries',
        rest: 'and serves them from memory, fresh as the data changes, with no code changes.',
      }

  return (
    <>
      <PageBar
        title="Readyset Platform"
        meta={<span>{meta}</span>}
        actions={actions}
      />
      <PageContainer className="pt-9 pb-24">
        {(dockerLine || blocked) && !ready && !starting && (
          <InlineNotice
            tone="quiet"
            accent="warning"
            title=""
            message={dockerLine ?? blocked ?? ''}
            action={
              dockerLine
                ? {
                    label: 'Get Docker Desktop',
                    onClick: () =>
                      window.open(DOCKER_DESKTOP_URL, '_blank', 'noopener'),
                  }
                : undefined
            }
            className="mb-7"
          />
        )}
        <Verdict lead={verdict.lead} className="max-w-[72ch]">
          {verdict.rest}
        </Verdict>
        {starting && (
          <StartingCard
            containers={d.containers}
            phase={d.phase}
            loadRunning={d.loadRunning}
            percent={d.provisionPct}
            startedAt={startedAt}
          />
        )}
        {ready && (
          <>
            <div id="platform-comparison">
              <ComparisonCard
                samples={d.samples}
                events={d.events}
                metric={metric}
                onMetric={setMetric}
              />
            </div>
            <PatternList
              patterns={d.patterns}
              mode={d.mode}
              cacheBudget={d.cacheBudget}
              querypilotOn={d.querypilotOn}
              highlightedKeys={
                tourStep === 'manual' || tourStep === 'manualResult'
                  ? suggestions.map((row) => row.key)
                  : []
              }
              busy={modeResetting}
              onCache={d.cache}
              onUncache={d.uncache}
              onPolicy={onPolicy}
            />
          </>
        )}
        {!starting && !ready && (
          <FactsCard
            checks={checks}
            facts={facts.data}
            queries={
              facts.data?.query_count ?? (d.patterns.length || WORKLOAD_QUERIES)
            }
          />
        )}
        <WhatToTry
          items={tryItems(d.patterns)}
          enabled={ready}
          activeId={activeTry}
          done={done}
          onTry={onTry}
        />
      </PageContainer>
      {ready &&
        d.events.length > 0 &&
        tourDone === true &&
        tourStep == null && <DemoTrialInvite />}
      <ConfirmDialog
        tone="quiet"
        isOpen={pendingMode != null}
        onClose={() => setPendingMode(null)}
        onConfirm={() => void confirmMode()}
        title="Switch caching policy?"
        subtitle={MODE_SWITCH_CONFIRM}
        confirmLabel="Switch policy"
        confirmVariant="primary"
      />
      <ConfirmDialog
        tone="quiet"
        isOpen={confirmingStart}
        onClose={() => setConfirmingStart(false)}
        onConfirm={() => {
          setConfirmingStart(false)
          start()
        }}
        title="Start the demo?"
        subtitle={
          checks?.images_present
            ? 'Runs four containers on your Mac for up to an hour. The images are already downloaded.'
            : `Downloads about ${formatDownloadSize(checks?.download_mb)} of images, then runs four containers on your Mac for up to an hour.`
        }
        confirmLabel="Start the demo"
        confirmVariant="primary"
      />
      <ShutdownDialog
        isOpen={confirmingShutdown}
        onClose={() => setConfirmingShutdown(false)}
        onConfirm={() => {
          setConfirmingShutdown(false)
          void d.tearDown()
        }}
      />
    </>
  )
}
