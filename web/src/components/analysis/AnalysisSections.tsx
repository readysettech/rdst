/**
 * Shared analysis section components.
 *
 * Extracted from AnalysisResults.tsx so that both the standalone /results page
 * and the scan analysis modal can render rich analysis data without duplication.
 */

import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { DetailExpander } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { useId } from 'react'
import type {
  CompleteEvent,
  ExplainResults,
  IndexPlannerResult,
  IndexTesting,
  ReadysetCacheability,
  RewriteTesting,
  TestedRewrite,
} from '../../lib/api'
import { findPlannerResult, plannerVerificationOff } from '../../lib/indexTesting'
import { SQLDisplay } from '../SQLDisplay'

// ---------------------------------------------------------------------------
// Shared types & helpers
// ---------------------------------------------------------------------------

type TagVariant = 'positive' | 'informative' | 'warning' | 'negative'
type StyleVariant = 'positive' | 'info' | 'warning' | 'negative'

// Valid icon names from @rs/ui-icons
export type ValidIconName =
  | 'access'
  | 'add'
  | 'alert'
  | 'arrow-down'
  | 'arrow-left'
  | 'arrow-right'
  | 'arrow-up'
  | 'close'
  | 'database'
  | 'database-settings'
  | 'edit'
  | 'info'
  | 'key'
  | 'play'
  | 'search'
  | 'settings'
  | 'sparkles'
  | 'speedometer'
  | 'tick-double'
  | 'tick'
  | 'trash'
  | 'layers'
  | 'dashboard'
  | 'observe'
  | 'querypilot'
  | 'test-tube'

export const getRatingVariant = (rating: string): TagVariant => {
  switch (rating?.toLowerCase()) {
    case 'excellent':
      return 'positive'
    case 'good':
      return 'informative'
    case 'fair':
      return 'warning'
    case 'poor':
      return 'negative'
    default:
      return 'informative'
  }
}

export const getRatingIcon = (rating: string): ValidIconName => {
  switch (rating?.toLowerCase()) {
    case 'excellent':
      return 'sparkles'
    case 'good':
      return 'tick-double'
    case 'fair':
      return 'alert'
    case 'poor':
      return 'close'
    default:
      return 'info'
  }
}

export const getPriorityVariant = (priority: string): TagVariant => {
  switch (priority?.toLowerCase()) {
    case 'high':
      return 'negative'
    case 'medium':
      return 'warning'
    case 'low':
      return 'positive'
    default:
      return 'informative'
  }
}

export const getScoreVariant = (score: number): StyleVariant => {
  if (score >= 80) return 'positive'
  if (score >= 50) return 'warning'
  return 'negative'
}

export const variantStyles: Record<
  StyleVariant,
  { bg: string; text: string; border: string; glow: string }
> = {
  positive: {
    bg: 'bg-surface-positive-soft',
    text: 'text-content-positive-soft',
    border: 'border-border-positive-soft',
    glow: 'shadow-glow-positive',
  },
  info: {
    bg: 'bg-surface-info-soft',
    text: 'text-content-info-soft',
    border: 'border-border-info-soft',
    glow: 'shadow-glow-info',
  },
  warning: {
    bg: 'bg-surface-warning-soft',
    text: 'text-content-warning-soft',
    border: 'border-border-warning-soft',
    glow: 'shadow-glow-warning',
  },
  negative: {
    bg: 'bg-surface-negative-soft',
    text: 'text-content-negative-soft',
    border: 'border-border-negative-soft',
    glow: 'shadow-glow-negative',
  },
}

// ---------------------------------------------------------------------------
// Normalize rewrite testing data from various backend shapes
// ---------------------------------------------------------------------------

export function normalizeRewriteTesting(
  candidate: unknown
): RewriteTesting | undefined {
  if (!candidate || typeof candidate !== 'object') {
    return undefined
  }

  const testing = candidate as RewriteTesting & {
    success?: boolean
    rewrite_results?: unknown
    best_rewrite?: unknown
  }

  if (typeof testing.tested === 'boolean') {
    return testing
  }

  if (testing.skipped_reason || testing.success === false) {
    return { ...testing, tested: false }
  }

  if (testing.success === true) {
    const rewriteResults = Array.isArray(testing.rewrite_results)
      ? testing.rewrite_results
      : []
    return {
      ...testing,
      tested: rewriteResults.length > 0 || Boolean(testing.best_rewrite),
      rewrite_results: rewriteResults as RewriteTesting['rewrite_results'],
    }
  }

  return undefined
}

export function resolveRewriteTesting(
  ...candidates: Array<RewriteTesting | undefined>
): RewriteTesting | undefined {
  for (const c of candidates) {
    const normalized = normalizeRewriteTesting(c)
    if (normalized) return normalized
  }
  return undefined
}

// ---------------------------------------------------------------------------
// ScoreGauge — animated circular score indicator
// ---------------------------------------------------------------------------

export function ScoreGauge({
  score,
  size = 80,
  variant: variantOverride,
}: {
  score: number
  size?: number
  // When set, the ring/number use this variant instead of the raw score band,
  // so the gauge agrees with the surrounding rating verdict. [QW20]
  variant?: StyleVariant
}) {
  const variant = variantOverride ?? getScoreVariant(score)
  const style = variantStyles[variant]
  const radius = (size - 8) / 2
  const circumference = radius * 2 * Math.PI
  const offset = circumference - (score / 100) * circumference

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="6"
          className="text-surface-layout-2"
        />
        <m.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth="6"
          strokeLinecap="round"
          className={style.text}
          stroke="currentColor"
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          style={{ strokeDasharray: circumference }}
          transition={{ duration: 1, ease: 'easeOut', delay: 0.2 }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <m.span
          className={`${size >= 96 ? 'text-stat-hero' : 'text-xl font-bold'} tabular-nums ${style.text}`}
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.4, delay: 0.5 }}
        >
          {score}
        </m.span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MetricCard — small stat tile
// ---------------------------------------------------------------------------

export function MetricCard({
  label,
  value,
  icon,
  delay = 0,
}: {
  label: string
  value: string | number
  icon?: 'speedometer' | 'layers' | 'dashboard' | 'observe'
  delay?: number
}) {
  return (
    <m.div
      className="bg-surface-layout-2 rounded-xl p-4 border border-border-layout-1 hover:border-border-layout-2 transition-colors"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay }}
    >
      <HStack className="gap-2 mb-2 items-center">
        {icon && (
          <Icon
            name={icon}
            label={label}
            className="w-4 h-4 text-content-layout-3"
          />
        )}
        <Text
          level="overline"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          {label}
        </Text>
      </HStack>
      <Text level="mono-large" className="text-content-layout-1 font-semibold">
        {String(value)}
      </Text>
    </m.div>
  )
}

// ---------------------------------------------------------------------------
// SectionHeader — consistent section heading
// ---------------------------------------------------------------------------

export function SectionHeader({
  icon,
  title,
  subtitle,
  action,
}: {
  icon: ValidIconName
  title: string
  subtitle?: string
  action?: React.ReactNode
}) {
  return (
    <HStack className="justify-between items-start mb-5">
      <HStack className="gap-3 items-center">
        <div className="w-10 h-10 rounded-xl bg-surface-layout-2 flex items-center justify-center">
          <Icon
            name={icon}
            label={title}
            className="w-5 h-5 text-content-layout-2"
          />
        </div>
        <VStack className="gap-0.5 items-start">
          <Text as="h2" level="headline-4" className="text-content-layout-1">
            {title}
          </Text>
          {subtitle && (
            <Text level="body-small" className="text-content-layout-3">
              {subtitle}
            </Text>
          )}
        </VStack>
      </HStack>
      {action}
    </HStack>
  )
}

// ---------------------------------------------------------------------------
// PerformanceSummarySection — hero section with score gauge + metrics
// ---------------------------------------------------------------------------

interface PerformanceSummarySectionProps {
  perf: {
    overall_rating?: string
    efficiency_score?: number
    primary_concerns?: string[]
  }
  explainResults?: ExplainResults
}

export function PerformanceSummarySection({
  perf,
  explainResults,
}: PerformanceSummarySectionProps) {
  const ratingVariant = perf.overall_rating
    ? getRatingVariant(perf.overall_rating)
    : 'informative'
  const ratingStyle =
    ratingVariant === 'informative'
      ? variantStyles.info
      : variantStyles[ratingVariant as StyleVariant]

  return (
    <m.div
      className="bg-surface-raised rounded-2xl shadow-elevation-1 overflow-hidden"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
    >
      {/* Header with rating */}
      <div className={`${ratingStyle.bg} p-6 border-b ${ratingStyle.border}`}>
        <HStack className="justify-between items-center">
          <HStack className="gap-5 items-center">
            {perf.efficiency_score !== undefined && (
              <ScoreGauge
                score={perf.efficiency_score}
                size={104}
                variant={
                  ratingVariant === 'informative'
                    ? 'info'
                    : (ratingVariant as StyleVariant)
                }
              />
            )}
            <VStack className="gap-1 items-start">
              <Text level="headline-3" className="text-content-layout-1">
                Performance Summary
              </Text>
              {perf.overall_rating && (
                <HStack className="gap-2 items-center">
                  <Icon
                    name={getRatingIcon(perf.overall_rating)}
                    label={perf.overall_rating}
                    className={`w-4 h-4 ${ratingStyle.text}`}
                  />
                  <Text level="label-medium" className={ratingStyle.text}>
                    {perf.overall_rating.charAt(0).toUpperCase() +
                      perf.overall_rating.slice(1)}{' '}
                    Performance
                  </Text>
                </HStack>
              )}
            </VStack>
          </HStack>
          {perf.overall_rating && (
            <Tag
              variant={ratingVariant}
              label={perf.overall_rating.toUpperCase()}
            />
          )}
        </HStack>
      </div>

      {/* Metrics grid */}
      <div className="p-6">
        {explainResults && (
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 mb-6">
            <MetricCard
              label="Execution Time"
              value={`${explainResults.execution_time_ms?.toFixed(2) || '0'}ms`}
              icon="speedometer"
              delay={0.1}
            />
            <MetricCard
              label="Rows Examined"
              value={explainResults.rows_examined?.toLocaleString() || '0'}
              icon="layers"
              delay={0.15}
            />
            <MetricCard
              label="Rows Returned"
              value={explainResults.rows_returned?.toLocaleString() || '0'}
              icon="dashboard"
              delay={0.2}
            />
            <MetricCard
              label="Cost Estimate"
              value={explainResults.cost_estimate?.toFixed(2) || '0'}
              icon="observe"
              delay={0.25}
            />
          </div>
        )}

        {/* Concerns section */}
        {perf.primary_concerns && perf.primary_concerns.length > 0 && (
          <m.div
            className="bg-surface-warning-soft/30 rounded-xl p-5 border border-border-warning-soft"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
          >
            <HStack className="gap-3 items-center mb-4">
              <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center">
                <Icon
                  name="alert"
                  label="Concerns"
                  className="w-4 h-4 text-content-warning-soft"
                />
              </div>
              <Text level="label-medium" className="text-content-warning-soft">
                Performance Concerns
              </Text>
            </HStack>
            <ul className="space-y-2.5">
              {perf.primary_concerns.map((concern, i) => (
                <m.li
                  key={i}
                  className="flex items-start gap-3"
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.35 + 0.05 * i }}
                >
                  <span className="text-content-warning-soft mt-1">•</span>
                  <Text
                    level="body-small"
                    className="text-content-layout-2 leading-relaxed"
                  >
                    {concern}
                  </Text>
                </m.li>
              ))}
            </ul>
          </m.div>
        )}
      </div>
    </m.div>
  )
}

// ---------------------------------------------------------------------------
// TestedOptimizationsSection — rewrite comparison table
// ---------------------------------------------------------------------------

export function TestedOptimizationsSection({
  testing,
}: {
  testing: RewriteTesting
}) {
  if (!testing.tested) {
    if (testing.skipped_reason === 'parameterized_query') {
      return (
        <m.div
          className="bg-surface-warning-soft/50 border border-border-warning-soft rounded-xl p-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
        >
          <HStack className="gap-3 items-start">
            <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center shrink-0">
              <Icon
                name="alert"
                label="Warning"
                className="w-4 h-4 text-content-warning-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text level="label-medium" className="text-content-warning-soft">
                Rewrite Testing Skipped
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                Query contains parameter placeholders ($1, $2 or ?) without
                actual values.
              </Text>
            </VStack>
          </HStack>
        </m.div>
      )
    }
    return null
  }

  const rewriteResults = testing.rewrite_results || []
  const originalTime = testing.original_performance?.execution_time_ms || 0
  const baselineRowsReturned = testing.original_performance?.rows_returned

  const isRowCountMismatch = (baseline: unknown, rewrite: unknown): boolean =>
    typeof baseline === 'number' &&
    typeof rewrite === 'number' &&
    baseline >= 0 &&
    rewrite >= 0 &&
    baseline !== rewrite

  const bestRewriteRowsReturned =
    testing.best_rewrite?.performance?.rows_returned
  const bestRewriteMismatch = isRowCountMismatch(
    baselineRowsReturned,
    bestRewriteRowsReturned
  )

  if (rewriteResults.length === 0) {
    return (
      <Card>
        <Card.Content>
          <SectionHeader
            icon="test-tube"
            title="Tested Optimizations"
            subtitle="Query rewrite performance comparison"
          />
          <div className="bg-surface-info-soft/50 border border-border-info-soft rounded-xl p-5">
            <HStack className="gap-3 items-center">
              <Icon
                name="info"
                label="Info"
                className="w-5 h-5 text-content-info-soft"
              />
              <Text level="body-small" className="text-content-info-soft">
                No rewrites were tested successfully
              </Text>
            </HStack>
          </div>
        </Card.Content>
      </Card>
    )
  }

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2 }}
    >
      {bestRewriteMismatch && (
        <div className="rounded-xl border border-border-negative-soft bg-surface-negative-soft/40 p-4">
          <HStack className="gap-3 items-start">
            <div className="w-8 h-8 rounded-lg bg-surface-negative-soft flex items-center justify-center">
              <Icon
                name="alert"
                label="Mismatch"
                className="w-4 h-4 text-content-negative-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text level="label-medium" className="text-content-negative-soft">
                Row count mismatch detected
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                Best rewrite returns{' '}
                {bestRewriteRowsReturned?.toLocaleString() ?? '?'} rows, but the
                original returned{' '}
                {baselineRowsReturned?.toLocaleString() ?? '?'}. Review
                carefully before adopting this rewrite.
              </Text>
            </VStack>
          </HStack>
        </div>
      )}
      <SectionHeader
        icon="test-tube"
        title="Tested Optimizations"
        subtitle={`${rewriteResults.length} rewrite${rewriteResults.length > 1 ? 's' : ''} tested against original`}
      />
      <div className="space-y-3">
        {rewriteResults.map((rewrite: TestedRewrite, i: number) => {
          const improvement = rewrite.improvement?.overall?.improvement_pct || 0
          const rewriteTime = rewrite.performance?.execution_time_ms || 0
          const rewriteRowsReturned = rewrite.performance?.rows_returned
          const rowCountMismatch = isRowCountMismatch(
            baselineRowsReturned,
            rewriteRowsReturned
          )

          let status: {
            icon: ValidIconName
            text: string
            variant: TagVariant
          }
          if (improvement >= 10) {
            status = {
              icon: 'arrow-up',
              text: 'FASTER',
              variant: 'positive',
            }
          } else if (improvement >= 0) {
            status = {
              icon: 'arrow-right',
              text: 'SIMILAR',
              variant: 'informative',
            }
          } else {
            status = {
              icon: 'arrow-down',
              text: 'SLOWER',
              variant: 'negative',
            }
          }

          const styleVariant: StyleVariant =
            status.variant === 'informative' ? 'info' : status.variant
          const style = variantStyles[styleVariant]

          return (
            <m.div
              key={i}
              className={`bg-surface-layout-1 rounded-xl overflow-hidden border border-border-layout-1 hover:border-border-layout-2 transition-all ${style.glow}`}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3, delay: 0.1 * i }}
            >
              <div className="p-5 border-b border-border-layout-1">
                <HStack className="justify-between items-start gap-4">
                  <VStack className="gap-3 items-start flex-1">
                    <HStack className="gap-3 items-center">
                      <div
                        className={`w-8 h-8 rounded-lg ${style.bg} flex items-center justify-center`}
                      >
                        <Icon
                          name={status.icon}
                          label={status.text}
                          className={`w-4 h-4 ${style.text}`}
                        />
                      </div>
                      <Tag variant={status.variant} label={status.text} />
                      <span
                        className={`${style.text} font-mono text-sm font-medium`}
                      >
                        {improvement >= 0 ? '+' : ''}
                        {improvement.toFixed(1)}%
                      </span>
                    </HStack>
                    <Text level="body-small" className="text-content-layout-2">
                      {rewrite.suggestion_metadata?.explanation ||
                        'Query rewrite optimization'}
                    </Text>
                  </VStack>
                  <VStack className="gap-1 items-end shrink-0">
                    <HStack className="gap-2 items-baseline">
                      <Text
                        level="mono-large"
                        className="text-content-layout-1 font-semibold"
                      >
                        {rewriteTime.toFixed(2)}
                      </Text>
                      <Text level="caption" className="text-content-layout-3">
                        ms
                      </Text>
                    </HStack>
                    {originalTime > 0 && (
                      <Text level="caption" className="text-content-layout-3">
                        vs {originalTime.toFixed(2)}ms original
                      </Text>
                    )}
                    {rowCountMismatch && (
                      <Tag
                        size="small"
                        variant="negative"
                        modifier="ghost"
                        label={`Returns ${rewriteRowsReturned?.toLocaleString() ?? '?'} rows (${baselineRowsReturned?.toLocaleString() ?? '?'} original)`}
                      />
                    )}
                  </VStack>
                </HStack>
              </div>
              <div className="relative group">
                <SQLDisplay
                  sql={rewrite.sql}
                  className="p-4 bg-surface-layout-2"
                  showCopy
                />
              </div>
            </m.div>
          )
        })}
      </div>
    </m.div>
  )
}

// ---------------------------------------------------------------------------
// IndexRecommendationsSection — index SQL + rationale
// ---------------------------------------------------------------------------

function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined) return '?'
  return Math.round(value).toLocaleString()
}

export function PlannerVerdict({ result }: { result: IndexPlannerResult }) {
  if (result.error) {
    return (
      <HStack className="gap-2 items-center px-4 py-2.5 bg-surface-warning-soft/30 border-t border-border-warning-soft">
        <Icon name="alert" label="Planner check" className="w-3.5 h-3.5 text-content-warning-soft" />
        <Text level="body-small" className="text-content-layout-2">
          hypopg check could not test this index: {result.error}
        </Text>
      </HStack>
    )
  }
  if (result.planner_used_index) {
    const pct = result.cost_reduction_pct
    return (
      <HStack className="gap-2 items-center px-4 py-2.5 bg-surface-positive-soft/30 border-t border-border-positive-soft">
        <Icon name="tick" label="Planner check" className="w-3.5 h-3.5 text-content-positive-soft" />
        <Text level="body-small" className="text-content-layout-2">
          hypopg check: the planner uses this index ({result.scan_type}).
          Estimated cost {formatCost(result.cost_before)} to{' '}
          {formatCost(result.cost_after)}
          {pct !== null && pct !== undefined ? ` (${pct}% lower)` : ''}. No index
          was created.
        </Text>
      </HStack>
    )
  }
  return (
    <HStack className="gap-2 items-center px-4 py-2.5 bg-surface-warning-soft/30 border-t border-border-warning-soft">
      <Icon name="alert" label="Planner check" className="w-3.5 h-3.5 text-content-warning-soft" />
      <Text level="body-small" className="text-content-layout-2">
        hypopg check: the planner would not use this index for this query. No
        index was created.
      </Text>
    </HStack>
  )
}

export function IndexRecommendationsSection({
  recommendations,
  indexTesting,
}: {
  recommendations: NonNullable<
    CompleteEvent['llm_analysis']
  >['index_recommendations']
  indexTesting?: IndexTesting | null
}) {
  if (!recommendations || recommendations.length === 0) return null
  const hypopgMissing = plannerVerificationOff(indexTesting)

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.3 }}
    >
      <SectionHeader
        icon="search"
        title="Index Recommendations"
        subtitle={`${recommendations.length} suggested index${recommendations.length > 1 ? 'es' : ''} for optimization`}
      />
      <div className="space-y-3">
        {recommendations.map((index, i) => (
          <m.div
            key={i}
            className="bg-surface-layout-1 rounded-xl overflow-hidden border border-border-layout-1 hover:border-border-layout-2 transition-all"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: 0.1 * i }}
          >
            <div className="p-5 border-b border-border-layout-1">
              <HStack className="justify-between items-start gap-4 mb-3">
                <HStack className="gap-3 items-center">
                  <div className="w-8 h-8 rounded-lg bg-surface-info-soft flex items-center justify-center">
                    <Icon
                      name="database"
                      label="Index"
                      className="w-4 h-4 text-content-info-soft"
                    />
                  </div>
                  <VStack className="gap-0.5 items-start">
                    <Text
                      as="span"
                      level="label-medium"
                      className="text-content-layout-1"
                    >
                      Index on{' '}
                      <Text
                        as="span"
                        level="mono-small"
                        className="text-content-primary-soft"
                      >
                        {index.table}
                      </Text>
                    </Text>
                  </VStack>
                </HStack>
                <Tag
                  variant={getPriorityVariant(index.estimated_impact)}
                  modifier="ghost"
                  size="small"
                  label={`${index.estimated_impact.toUpperCase()} IMPACT`}
                />
              </HStack>
              <Text
                level="body-small"
                className="text-content-layout-2 leading-relaxed"
              >
                {index.rationale}
              </Text>
            </div>
            <div className="relative">
              <SQLDisplay
                sql={index.sql}
                className="p-4 bg-surface-layout-2"
                showCopy
              />
            </div>
            {(() => {
              const verdict = findPlannerResult(indexTesting, index)
              return verdict ? <PlannerVerdict result={verdict} /> : null
            })()}
            {index.caveats && index.caveats.length > 0 && (
              <div className="p-4 bg-surface-warning-soft/30 border-t border-border-warning-soft">
                <HStack className="gap-2 items-center mb-2">
                  <Icon
                    name="alert"
                    label="Caveats"
                    className="w-3.5 h-3.5 text-content-warning-soft"
                  />
                  <Text
                    level="overline"
                    className="text-content-warning-soft uppercase tracking-wider"
                  >
                    Caveats
                  </Text>
                </HStack>
                <ul className="space-y-1.5 ml-5">
                  {index.caveats.map((c, j) => (
                    <li key={j} className="text-content-layout-2 list-disc">
                      <Text as="span" level="body-small">
                        {c}
                      </Text>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </m.div>
        ))}
      </div>
      {hypopgMissing && (
        <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4">
          <HStack className="gap-2 items-start">
            <Icon name="info" label="Planner check" className="w-4 h-4 text-content-info-soft mt-0.5" />
            <VStack className="gap-2 items-start">
              <Text level="body-small" className="text-content-layout-2">
                hypopg check unavailable: {indexTesting?.message}
              </Text>
              {indexTesting?.install_sql && (
                <SQLDisplay sql={indexTesting.install_sql} className="p-2 bg-surface-layout-2 rounded-lg" showCopy />
              )}
            </VStack>
          </HStack>
        </div>
      )}
    </m.div>
  )
}

// ---------------------------------------------------------------------------
// AdditionalRecommendationsSection — optimization opportunities
// ---------------------------------------------------------------------------

export function AdditionalRecommendationsSection({
  opportunities,
  collapsible = false,
}: {
  opportunities: NonNullable<
    CompleteEvent['llm_analysis']
  >['optimization_opportunities']
  /** On /results these are the *other* recs — collapse them behind a
   *  "More recommendations (N) ▾" disclosure so they sit under Details rather
   *  than as a full peer section. Scan modal keeps the default (expanded). */
  collapsible?: boolean
}) {
  const [open, setOpen] = useDisclosure({})
  if (!opportunities || opportunities.length === 0) return null

  const list = (
    <div className="bg-surface-layout-1 rounded-xl border border-border-layout-1 overflow-hidden">
      {opportunities.map((opp, i) => {
        const priorityVariant: StyleVariant =
          opp.priority?.toLowerCase() === 'high'
            ? 'negative'
            : opp.priority?.toLowerCase() === 'medium'
              ? 'warning'
              : 'info'
        const style = variantStyles[priorityVariant]

        return (
          <m.div
            key={i}
            className={`p-4 ${i > 0 ? 'border-t border-border-layout-1' : ''} hover:bg-surface-layout-2/50 transition-colors`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2, delay: 0.05 * i }}
          >
            <HStack className="gap-4 items-start">
              <div
                className={`w-7 h-7 rounded-lg ${style.bg} flex items-center justify-center shrink-0 mt-0.5`}
              >
                <Text level="caption" className={`${style.text} font-bold`}>
                  {opp.priority?.charAt(0).toUpperCase() || 'M'}
                </Text>
              </div>
              <VStack className="gap-1.5 items-start flex-1">
                <HStack className="gap-2 items-center">
                  <Tag
                    variant={getPriorityVariant(opp.priority)}
                    modifier="ghost"
                    size="small"
                    label={opp.priority?.toUpperCase() || 'MEDIUM'}
                  />
                </HStack>
                <Text
                  as="span"
                  level="body-small"
                  className="text-content-layout-2 leading-relaxed"
                >
                  {opp.description}
                </Text>
              </VStack>
            </HStack>
          </m.div>
        )
      })}
    </div>
  )

  // Collapsed variant: a quiet "More recommendations (N) ▾" disclosure that
  // keeps these off the default view until asked for. [brief, VIS-107]
  if (collapsible) {
    return (
      <m.div
        className="space-y-3"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.4 }}
      >
        <Pressable
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 text-content-layout-2 hover:text-content-layout-1 transition-colors"
        >
          <Icon
            name="sparkles"
            label="More recommendations"
            className="w-4 h-4 text-content-layout-3"
          />
          <Text as="span" level="label-medium">
            More recommendations ({opportunities.length})
          </Text>
          <Icon
            name="chevron-down"
            label=""
            className={`w-4 h-4 text-content-layout-3 transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </Pressable>
        {open && list}
      </m.div>
    )
  }

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.4 }}
    >
      <SectionHeader
        icon="sparkles"
        title="Additional Recommendations"
        subtitle="Other optimization opportunities identified"
      />
      {list}
    </m.div>
  )
}

// ---------------------------------------------------------------------------
// ReadysetCacheabilitySection — compatibility estimate or verification
// ---------------------------------------------------------------------------

export function ReadysetCacheabilitySection({
  cacheability,
  cacheDeployed,
  onCacheQuery,
  onDeployNavigate,
  onSetUpCaching,
  isCaching,
}: {
  cacheability: ReadysetCacheability
  cacheDeployed?: boolean
  onCacheQuery?: () => void
  onDeployNavigate?: () => void
  onSetUpCaching?: () => void
  isCaching?: boolean
}) {
  const detailId = useId()
  // A definitive "Not Cacheable / BLOCKED" verdict is only trustworthy when the
  // check completed with a confident, clean answer. An errored or low-confidence
  // result (a returned "no: db error" row, Readyset startup/timeout, unknown
  // status) must read as "Not Verified / UNAVAILABLE", never a false definitive
  // BLOCKED (P69). A genuine unsupported verdict (confidence "high", no error
  // signal) still renders as BLOCKED.
  const CHECK_ERROR =
    /db error|connection|timeout|timed out|unreachable|refused|startup|unavailable|pending|failed|\berror\b/i
  const verdictLooksUnreliable =
    cacheability.cacheable === false &&
    (cacheability.confidence === 'low' ||
      cacheability.confidence === 'unknown' ||
      CHECK_ERROR.test(cacheability.explanation ?? '') ||
      (cacheability.issues ?? []).some((issue) => CHECK_ERROR.test(issue)))
  const isVerified =
    cacheability.checked &&
    cacheability.method !== 'static_analysis' &&
    cacheability.method !== 'readyset_unavailable' &&
    !verdictLooksUnreliable
  const isEstimated =
    cacheability.checked && cacheability.method === 'static_analysis'
  const isCacheable = isVerified && cacheability.cacheable === true
  const isPositive =
    isCacheable || (isEstimated && cacheability.cacheable === true)
  // Keep raw driver/client text out of the primary copy (P41): the backend now
  // sends a human `explanation` plus raw `detail`; if a raw-looking explanation
  // still arrives (legacy payload / unreliable verdict), swap it for a generic
  // line and move the raw text behind the technical-details expander.
  const rawDetail =
    (cacheability as ReadysetCacheability & { detail?: string | null })
      .detail ?? undefined
  const explanationLooksRaw =
    !isVerified &&
    Boolean(cacheability.explanation) &&
    CHECK_ERROR.test(cacheability.explanation ?? '')
  const staticBodyText = isEstimated
    ? cacheability.cacheable
      ? 'Static SQL screening found no obvious Readyset blockers. Run a temporary comparison to verify support and measure performance.'
      : 'Static SQL screening found potential Readyset blockers. Review them before trying Readyset.'
    : undefined
  const bodyText = staticBodyText
    ? staticBodyText
    : explanationLooksRaw && rawDetail === undefined
      ? "Readyset could not complete the cacheability check, so this query's cacheability has not been verified."
      : cacheability.explanation
  const technicalDetail =
    rawDetail ??
    (explanationLooksRaw ? (cacheability.explanation ?? undefined) : undefined)
  const variant: StyleVariant = isPositive
    ? 'positive'
    : !isVerified
      ? 'warning'
      : 'negative'
  const style = variantStyles[variant]
  const verdict = isEstimated
    ? cacheability.cacheable
      ? 'No obvious blockers'
      : 'Potential blockers'
    : !isVerified
      ? 'Not Verified'
      : isCacheable
        ? 'Readyset compatible'
        : 'Unsupported by Readyset'

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.5 }}
    >
      <SectionHeader
        icon="layers"
        title="Readyset Compatibility"
        subtitle="Static SQL screening — Docker is only needed for a comparison"
      />
      <div
        className={`rounded-xl overflow-hidden border ${style.border} ${style.glow}`}
      >
        <div className={`${style.bg} p-6`}>
          <HStack className="justify-between items-center mb-4">
            <HStack className="gap-4 items-center">
              <m.div
                className={`w-14 h-14 rounded-2xl ${isPositive ? 'bg-surface-positive-soft' : !isVerified ? 'bg-surface-warning-soft' : 'bg-surface-negative-soft'} flex items-center justify-center`}
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{
                  type: 'spring',
                  stiffness: 400,
                  damping: 15,
                  delay: 0.2,
                }}
              >
                <Icon
                  name={
                    isPositive ? 'tick-double' : !isVerified ? 'alert' : 'close'
                  }
                  label={verdict}
                  className={`w-7 h-7 ${style.text}`}
                />
              </m.div>
              <VStack className="gap-1 items-start">
                <Text level="headline-4" className={style.text}>
                  {verdict}
                </Text>
                {isVerified && cacheability.confidence && (
                  <Text level="caption" className="text-content-layout-3">
                    {cacheability.confidence.charAt(0).toUpperCase() +
                      cacheability.confidence.slice(1)}{' '}
                    confidence
                  </Text>
                )}
              </VStack>
            </HStack>
            <HStack className="gap-2 items-center">
              <Tag
                variant={
                  isPositive ? 'positive' : !isVerified ? 'warning' : 'negative'
                }
                label={
                  isEstimated
                    ? 'STATIC CHECK'
                    : !isVerified
                      ? 'UNAVAILABLE'
                      : isCacheable
                        ? 'VERIFIED'
                        : 'UNSUPPORTED'
                }
              />
            </HStack>
          </HStack>
          {bodyText && (
            <Text
              level="body-small"
              className="text-content-layout-2 leading-relaxed"
            >
              {bodyText}
            </Text>
          )}
          {technicalDetail && (
            <div className="mt-3">
              <DetailExpander detail={technicalDetail} id={detailId} />
            </div>
          )}

          {/* Cache action buttons */}
          {(isCacheable || isEstimated) &&
            (onSetUpCaching || onCacheQuery || onDeployNavigate) && (
              <m.div
                className="mt-5 pt-5 border-t border-border-layout-1/30"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.6 }}
              >
                {onSetUpCaching ? (
                  <Button
                    variant="primary"
                    modifier="solid"
                    label="Try with Readyset"
                    icon="database-settings"
                    iconPosition="left"
                    onClick={onSetUpCaching}
                  />
                ) : cacheDeployed ? (
                  <Button
                    variant="primary"
                    modifier="solid"
                    label="Compare speed"
                    icon="add"
                    iconPosition="left"
                    onClick={onCacheQuery}
                    loading={isCaching}
                  />
                ) : (
                  <Button
                    variant="primary"
                    modifier="outline"
                    label="Open comparisons"
                    icon="database-settings"
                    iconPosition="left"
                    onClick={onDeployNavigate}
                  />
                )}
              </m.div>
            )}
        </div>
        {(isVerified || isEstimated) &&
          cacheability.issues &&
          cacheability.issues.length > 0 && (
            <div className="p-5 bg-surface-layout-1 border-t border-border-layout-1">
              <HStack className="gap-2 items-center mb-3">
                <Icon
                  name="alert"
                  label="Issues"
                  className="w-4 h-4 text-content-negative-soft"
                />
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Blocking Issues
                </Text>
              </HStack>
              <ul className="space-y-2">
                {cacheability.issues.map((issue, i) => (
                  <m.li
                    key={i}
                    className="flex items-start gap-2"
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.1 * i }}
                  >
                    <span className="text-content-negative-soft mt-1.5">•</span>
                    <Text level="body-small" className="text-content-layout-2">
                      {issue}
                    </Text>
                  </m.li>
                ))}
              </ul>
            </div>
          )}
      </div>
    </m.div>
  )
}
