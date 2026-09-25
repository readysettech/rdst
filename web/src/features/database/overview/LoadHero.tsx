// The page's hero (DESIGN_V5 section 2). Queries per second stacked by query, each
// query's share of database time stacked to 100%, or one latency line per query with
// the overall average dashed. The query list below is its legend. Drag across any chart to zoom;
// the list, shares and tiles re-scope to the window. Three small health tiles sit
// under the chart and switch it like the metric control does: six peers, one selected.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useEffect, useMemo, useState } from 'react'
import {
  type ChartOverlay,
  chartPauses,
  formatPercent,
  type LoadMetric,
  type LoadModel,
  MonitorTimeChart,
  OTHER_KEY,
  StackedLoadChart,
  type StackSeries,
  TONE_COLOR,
  timeShareRows,
} from '@/features/monitor-kit/charts'
import { formatMsWithUnit, formatQps } from '@/features/monitor-kit/format'
import { RollingNumber } from '@/features/monitor-kit/RollingNumber'
import type { MonitorHealth } from '@/lib/monitor/types'
import type { HealthPanel } from '../useDatabaseUrl'
import { ROLLBACK_EXPLAINER, type StripTile, stripTiles } from './healthStrip'

const SEGMENTS = [
  { value: 'calls', label: 'Queries/s' },
  { value: 'latency', label: 'Latency' },
  { value: 'time', label: 'Time spent' },
] satisfies Array<{ value: LoadMetric; label: string }>

const METRIC_TITLE: Record<LoadMetric, string> = {
  calls: 'Queries per second',
  latency: 'Latency',
  time: 'Time spent',
}

const SUBTITLE: Record<LoadMetric, string> = {
  calls: '',
  latency: 'Dashed: every query together',
  time: "Each query's share of database time",
}

function msLabel(value: number) {
  const formatted = formatMsWithUnit(value)
  return `${formatted.value} ${formatted.unit}`
}

/** Axis numbers without units (the unit sits above the axis). */
function axisNumber(value: number): string {
  if (value === 0) return '0'
  if (value >= 1000) return `${Math.round(value / 100) / 10}k`
  if (value >= 10) return String(Math.round(value))
  if (value >= 1) return String(Math.round(value * 10) / 10)
  return String(Math.round(value * 100) / 100)
}

function lastValue(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--)
    if (values[i] != null) return values[i]
  return null
}

function InfoTip({ label, text }: { label: string; text: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Pressable
            type="button"
            aria-label={label}
            className="inline-flex h-4 w-4 items-center justify-center rounded-full text-content-layout-3 transition-colors duration-fast hover:text-content-layout-1 focus-visible:shadow-focus focus-visible:outline-none"
          >
            <Icon
              name="info"
              label=""
              aria-hidden="true"
              className="h-3.5 w-3.5"
            />
          </Pressable>
        </TooltipTrigger>
        <TooltipContent label={text} className="max-w-xs" />
      </Tooltip>
    </TooltipProvider>
  )
}

function HealthTile({
  tile,
  selected,
  onSelect,
}: {
  tile: StripTile
  selected: boolean
  onSelect: () => void
}) {
  const toned = tile.tone !== 'neutral'
  const unit = tile.unit === '%' ? '%' : ` ${tile.unit}`
  const tip = [
    tile.footer,
    tile.toneReason,
    tile.id === 'rollback' ? ROLLBACK_EXPLAINER : tile.explain,
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <Pressable
      aria-pressed={selected}
      onClick={onSelect}
      aria-label={`${tile.title}: ${tile.value}${unit}. ${tile.footer}. ${tip ? `${tip} ` : ''}Show on the chart`}
      title={tip || undefined}
      data-testid={`health-tile-${tile.id}`}
      data-selected={selected || undefined}
      className={cn(
        'group relative flex min-w-0 items-center gap-2 overflow-hidden rounded-panel px-3 py-1.5 text-left',
        'transition-[background-color,box-shadow] duration-fast ease-base focus-visible:shadow-focus focus-visible:outline-none',
        selected
          ? 'bg-surface-primary-soft ring-1 ring-border-primary-soft ring-inset'
          : 'hover:bg-surface-layout-2'
      )}
    >
      <span
        aria-hidden="true"
        className="inline-block h-[3px] w-3 shrink-0 rounded-sm"
        style={{ background: tile.color }}
      />
      <span
        className={cn(
          'truncate text-label-small',
          selected ? 'text-content-primary-soft' : 'text-content-layout-2'
        )}
      >
        {tile.title}
      </span>
      <span className="ml-auto flex shrink-0 items-baseline gap-1">
        <span style={toned ? { color: TONE_COLOR[tile.tone] } : undefined}>
          <RollingNumber
            value={tile.value}
            className={cn(
              'text-label-large font-semibold tabular-nums',
              !toned && 'text-content-layout-1'
            )}
          />
        </span>
        <span className="text-caption text-content-layout-3">{tile.unit}</span>
      </span>
      {toned && (
        <span
          className="inline-flex shrink-0"
          style={{ color: TONE_COLOR[tile.tone] }}
        >
          <Icon
            name="alert"
            label={tile.toneReason ?? ''}
            className="h-3 w-3"
          />
        </span>
      )}
    </Pressable>
  )
}

/**
 * The hero's series for a metric. Latency draws only the focused query when there is
 * one, and only the overall line under an overlay (the overlay is the query's line).
 */
export function useHeroSeries(
  model: LoadModel | null | undefined,
  metric: LoadMetric,
  focusKey: string | null,
  overlaid = false
): StackSeries[] {
  return useMemo(() => {
    if (!model) return []
    if (metric === 'latency') {
      const keys = overlaid
        ? []
        : focusKey
          ? model.bands.filter((band) => band.key === focusKey)
          : model.bands.filter((band) => !band.isOther).slice(0, 6)
      return [
        ...keys.map((band) => ({
          id: band.key,
          label: band.label,
          color: band.color,
          values: model.latency[model.bands.indexOf(band)],
        })),
        {
          id: 'overall',
          label: 'All queries',
          color: 'var(--color-content-layout-2)',
          values: model.overallLatency,
          reference: true,
        },
      ]
    }
    const rows = metric === 'time' ? timeShareRows(model.load) : model.calls
    return model.bands.map((band, index) => ({
      id: band.key,
      label: band.label,
      color: band.color,
      values: rows[index],
    }))
  }, [model, metric, focusKey, overlaid])
}

/** The value card's number format and the axis unit for a metric. */
function metricFormat(metric: LoadMetric) {
  return {
    formatValue:
      metric === 'time'
        ? formatPercent
        : metric === 'calls'
          ? (value: number) => `${formatQps(value)}/s`
          : msLabel,
    axisUnit: (yMax: number) =>
      metric === 'time'
        ? { unit: '%', divisor: 1 }
        : metric === 'calls'
          ? { unit: '', divisor: 1 }
          : yMax >= 2000
            ? { unit: 's', divisor: 1000 }
            : { unit: 'ms', divisor: 1 },
  }
}

/** "Focused on <query>" with the way back to every query, or the way into the open one. */
function FocusChip({
  band,
  focused,
  onAll,
  onFocus,
}: {
  band: { label: string; color: string; line?: boolean }
  focused: boolean
  onAll: () => void
  onFocus: () => void
}) {
  if (!focused)
    return (
      <Button
        label="Focus the open query"
        size="small"
        modifier="ghost"
        onClick={onFocus}
        data-testid="chart-focus"
      />
    )
  return (
    <span
      className="inline-flex max-w-full items-center gap-2 rounded-pill border border-border-layout-2 bg-surface-layout-2 py-0.5 pr-0.5 pl-2.5"
      data-testid="chart-focused"
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block shrink-0',
          band.line ? 'h-[3px] w-3 rounded-sm' : 'h-2 w-2 rounded-full'
        )}
        style={{ background: band.color }}
      />
      <span className="min-w-0 max-w-32 truncate text-label-small text-content-layout-1 tablet:max-w-48">
        {band.label}
      </span>
      <Button
        label="All queries"
        icon="close"
        iconPosition="right"
        size="small"
        modifier="ghost"
        onClick={onAll}
        data-testid="chart-all-queries"
        className="h-6 px-2"
      />
    </span>
  )
}

/**
 * The hero's chart, small, for the sticky bar over the query list: once the hero has
 * scrolled away, the bands still map to the rows below.
 */
export function LoadStrip({
  model,
  metric,
  domain,
  res,
  title,
  hoverKey,
  onHoverKey,
  focusKey,
  overlay = null,
  onSelectKey,
  onShowChart,
}: {
  model: LoadModel | null
  metric: LoadMetric
  domain: [number, number]
  res: number
  title: string
  hoverKey: string | null
  onHoverKey: (key: string | null) => void
  focusKey: string | null
  /** The highlighted query's own line, when it has no band. */
  overlay?: ChartOverlay | null
  onSelectKey: (key: string) => void
  onShowChart: () => void
}) {
  const focused =
    focusKey && model?.bands.some((band) => band.key === focusKey)
      ? focusKey
      : null
  const series = useHeroSeries(model, metric, focused, Boolean(overlay))
  const { formatValue } = metricFormat(metric)
  if (!model) return null
  // The header names the query the strip shows: the overlay, a hovered band, the open one.
  const bandFor = (key: string | null) =>
    key ? model.bands.find((band) => band.key === key && !band.isOther) : null
  const shown: { label: string; color: string; line?: boolean } | null = overlay
    ? { label: overlay.label, color: overlay.color, line: true }
    : (bandFor(hoverKey) ?? bandFor(focused) ?? null)
  return (
    <div
      className="rounded-panel border border-border-layout-1 bg-surface-layout-1 px-2 pt-1.5"
      data-testid="load-strip"
    >
      <div className="flex items-center justify-between gap-2 px-1">
        <div className="flex min-w-0 items-center gap-2">
          <Text
            level="caption"
            className="shrink-0 truncate text-content-layout-2"
          >
            {title}
          </Text>
          {shown ? (
            <span
              className="inline-flex min-w-0 items-center gap-1.5 rounded-pill border border-border-layout-2 bg-surface-layout-2 px-2 py-px"
              data-testid="load-strip-highlight"
            >
              <span
                aria-hidden="true"
                className={cn(
                  'inline-block shrink-0',
                  shown.line ? 'h-[3px] w-3 rounded-sm' : 'h-2 w-2 rounded-full'
                )}
                style={{ background: shown.color }}
              />
              <span className="min-w-0 truncate text-label-small text-content-layout-1">
                {shown.label}
              </span>
            </span>
          ) : null}
        </div>
        <Button
          label="Full chart"
          size="small"
          modifier="link"
          onClick={onShowChart}
          className="shrink-0"
        />
      </div>
      <StackedLoadChart
        ariaLabel={title}
        xs={model.xs}
        series={series}
        mode={metric === 'latency' ? 'lines' : 'stack'}
        domain={domain}
        resolution={res}
        height={68}
        formatValue={formatValue}
        formatAxis={axisNumber}
        fixedMax={metric === 'time' ? 100 : undefined}
        hideShare={metric === 'time'}
        activeId={hoverKey}
        focusId={metric === 'latency' ? null : focused}
        overlay={overlay}
        overlayLabels={false}
        onHover={(id) => onHoverKey(id === 'overall' ? null : id)}
        onSelect={(id) =>
          id !== OTHER_KEY && id !== 'overall' && onSelectKey(id)
        }
        totalLabel="All queries"
        testId="load-strip-chart"
      />
    </div>
  )
}

export interface LoadHeroProps {
  model: LoadModel | null
  /** Same window with bands picked by call count; the Queries/s chart uses it. */
  callsModel?: LoadModel | null
  health: MonitorHealth | undefined
  loading: boolean
  domain: [number, number]
  /** 'last hour', or the zoom window's clock range. */
  scopeLabel: string
  zoomed: boolean
  onZoom: (window: [number, number]) => void
  onResetZoom: () => void
  metric: LoadMetric
  onMetricChange: (metric: LoadMetric) => void
  panel: HealthPanel | null
  onPanelChange: (panel: HealthPanel | null) => void
  hoverKey: string | null
  onHoverKey: (key: string | null) => void
  focusKey: string | null
  /** The highlighted query's own line, when it has no band in this metric. */
  overlay?: ChartOverlay | null
  onSelectKey: (key: string) => void
}

export function LoadHero({
  model: timeModel,
  callsModel,
  health,
  loading,
  domain,
  scopeLabel,
  zoomed,
  onZoom,
  onResetZoom,
  metric,
  onMetricChange,
  panel,
  onPanelChange,
  hoverKey,
  onHoverKey,
  focusKey,
  overlay: givenOverlay = null,
  onSelectKey,
}: LoadHeroProps) {
  const model = metric === 'calls' ? (callsModel ?? timeModel) : timeModel
  const tiles = useMemo(
    () => (health ? stripTiles(health, domain) : []),
    [health, domain]
  )
  const tile = panel ? tiles.find((item) => item.id === panel) : undefined
  // Spans with no readings, labelled with why when RDST knows (a lost connection).
  const pauses = useMemo(
    () =>
      health
        ? chartPauses({
            readings: health.series
              .filter((point) => point.qps != null)
              .map((point) => point.ts),
            res: health.res,
            domain,
            outages: health.outages,
            now: Date.now() / 1000,
          })
        : undefined,
    [health, domain]
  )

  // Opening a query focuses the chart on it; "All queries" steps back without closing it.
  const [released, setReleased] = useState<string | null>(null)
  useEffect(() => setReleased(null), [focusKey])
  const focusBand =
    focusKey && released !== focusKey
      ? model?.bands.find((band) => band.key === focusKey && !band.isOther)
      : undefined
  const focused = focusBand?.key ?? null
  // The open query's own line steps back with "All queries" too; a hovered row's doesn't.
  const overlay =
    givenOverlay && !(givenOverlay.id === focusKey && released === focusKey)
      ? givenOverlay
      : null
  const openBand = focusKey
    ? (model?.bands.find((band) => band.key === focusKey && !band.isOther) ??
      (givenOverlay?.id === focusKey
        ? { label: givenOverlay.label, color: givenOverlay.color, line: true }
        : undefined))
    : undefined
  const series = useHeroSeries(model, metric, focused, Boolean(overlay))

  const current = useMemo(() => {
    if (!model || !model.xs.length) return null
    if (metric === 'latency') {
      const value = lastValue(model.overallLatency)
      return value == null ? null : msLabel(value)
    }
    if (metric === 'time') return null
    const rows = model.calls
    let total = 0
    let any = false
    for (const row of rows) {
      const value = lastValue(row)
      if (value != null) {
        total += value
        any = true
      }
    }
    if (!any) return null
    return `${formatQps(total)} queries/s`
  }, [model, metric])

  const res = model?.res ?? health?.res ?? 60
  const title = tile
    ? tile.title
    : `${METRIC_TITLE[metric]}, ${scopeLabel.replace(/^in the /, '')}`
  const [headline, headlineUnit] = tile
    ? [tile.value, tile.unit]
    : current
      ? [current.split(' ')[0], current.split(' ').slice(1).join(' ')]
      : [null, null]

  return (
    <section
      aria-labelledby="load-hero-title"
      className="relative rounded-card border border-border-layout-1 bg-surface-layout-1 shadow-elevation-1"
      data-testid="load-hero"
    >
      <div className="space-y-2 px-4 pt-3.5 pb-2 tablet:px-5 tablet:pt-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <h2
                id="load-hero-title"
                className="text-headline-5 text-content-layout-1"
              >
                {title}
              </h2>
              {!tile && (
                <InfoTip
                  label="About this chart"
                  text="Each band is one query. Hover a band to find it in the list, click to open it, drag across the chart to zoom in."
                />
              )}
              {tile?.id === 'rollback' && (
                <InfoTip label="What rollbacks are" text={ROLLBACK_EXPLAINER} />
              )}
            </div>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              {headline && (
                <span className="inline-flex items-baseline gap-1">
                  <RollingNumber
                    value={headline}
                    className="text-headline-5 font-semibold tabular-nums text-content-layout-1"
                  />
                  {headlineUnit && (
                    <span className="text-label-medium text-content-layout-2">
                      {headlineUnit}
                    </span>
                  )}
                </span>
              )}
              <Text
                as="span"
                level="body-small"
                className="text-content-layout-2"
                data-testid="load-hero-subtitle"
              >
                {tile ? `${tile.subtitle}, ${scopeLabel}` : SUBTITLE[metric]}
                {(tile ? health?.detail_note : model?.detailNote) ? (
                  <span className="block text-caption text-content-layout-3">
                    {tile ? health?.detail_note : model?.detailNote}
                  </span>
                ) : null}
              </Text>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {openBand && !tile && (
              <FocusChip
                band={openBand}
                focused={Boolean(focused) || overlay?.id === focusKey}
                onAll={() => setReleased(focusKey)}
                onFocus={() => setReleased(null)}
              />
            )}
            {zoomed && (
              <Button
                label="Reset zoom"
                icon="close"
                iconPosition="right"
                size="small"
                modifier="outline"
                onClick={onResetZoom}
                data-testid="reset-zoom"
              />
            )}
            <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
              {/* No segment is selected while a health tile holds the chart. */}
              <SegmentedControl<string>
                className="w-max [&_button]:whitespace-nowrap"
                mode="radio"
                size="small"
                aria-label="Chart metric"
                value={tile ? '' : metric}
                onValueChange={(value) => {
                  if (tile) onPanelChange(null)
                  onMetricChange(value as LoadMetric)
                }}
                segments={SEGMENTS}
              />
            </div>
          </div>
        </div>

        <div key={tile?.id ?? metric} className="mk-fade relative z-10">
          {tile && health ? (
            <MonitorTimeChart
              ariaLabel={`${tile.title}, ${scopeLabel}`}
              series={[
                {
                  id: tile.id,
                  label: tile.title,
                  color: tile.color,
                  points: tile.points,
                  emphasis: true,
                },
              ]}
              domain={domain}
              resolution={health.res}
              yMin={tile.yMin}
              yMax={tile.yMax}
              formatValue={(value) => tile.formatPoint(value)}
              formatAxis={tile.formatAxis}
              onZoom={onZoom}
              pauses={pauses}
              height={220}
              hideEndLabels
            />
          ) : loading && !model ? (
            <Skeleton className="h-[220px] w-full rounded-panel" />
          ) : (
            <StackedLoadChart
              ariaLabel={title}
              xs={model?.xs ?? []}
              series={series}
              mode={metric === 'latency' ? 'lines' : 'stack'}
              domain={domain}
              resolution={res}
              height={220}
              formatValue={metricFormat(metric).formatValue}
              formatAxis={axisNumber}
              fixedMax={metric === 'time' ? 100 : undefined}
              hideShare={metric === 'time'}
              axisUnit={metricFormat(metric).axisUnit}
              activeId={hoverKey}
              focusId={metric === 'latency' ? null : focused}
              overlay={overlay}
              onHover={(id) => onHoverKey(id === 'overall' ? null : id)}
              onSelect={(id) =>
                id !== OTHER_KEY && id !== 'overall' && onSelectKey(id)
              }
              onZoom={onZoom}
              pauses={pauses}
              totalLabel="All queries"
            />
          )}
        </div>
      </div>

      <section
        aria-label="Database health"
        className="grid grid-cols-1 gap-1 rounded-b-card border-t border-border-layout-1 bg-surface-layout-2/40 p-1.5 tablet:grid-cols-3"
      >
        {!health
          ? [0, 1, 2].map((index) => (
              <Skeleton key={index} className="h-8 w-full rounded-panel" />
            ))
          : tiles.map((item) => (
              <HealthTile
                key={item.id}
                tile={item}
                selected={item.id === panel}
                onSelect={() => onPanelChange(item.id)}
              />
            ))}
      </section>
    </section>
  )
}
