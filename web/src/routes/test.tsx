/**
 * Test page for visually verifying ParameterDialog and ComparisonResult.
 * DEV only — tree-shaken in production builds.
 */

import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { m } from '@rs/ui-new/motion'
import { ParameterDialog } from '../components/top/ParameterDialog'
import type { CacheRunResult } from '../types/cache'

export const Route = createFileRoute('/test')({
  component: TestPage,
})

// --- Mock comparison results ---

const MOCK_CACHE_WINS: CacheRunResult = {
  success: true,
  query: 'SELECT * FROM orders WHERE shop_id = 759714 AND status_id = 2',
  iterations: 10,
  origin_stats: {
    mean: 142.5, median: 138.2, min: 98.4, max: 215.6,
    p50: 138.2, p95: 201.3, p99: 215.6, stddev: 35.2,
  },
  cache_stats: {
    mean: 0.82, median: 0.74, min: 0.51, max: 1.93,
    p50: 0.74, p95: 1.61, p99: 1.93, stddev: 0.38,
  },
  speedup_mean: 173.8,
  speedup_median: 186.8,
  improvement_pct: 17280,
  winner: 'readyset',
}

const MOCK_CACHE_WINS_MODERATE: CacheRunResult = {
  success: true,
  query: 'SELECT count(*) FROM users WHERE created_at > $1',
  iterations: 5,
  origin_stats: {
    mean: 45.3, median: 42.1, min: 32.8, max: 68.5,
    p50: 42.1, p95: 62.4, p99: 68.5, stddev: 12.1,
  },
  cache_stats: {
    mean: 8.7, median: 7.9, min: 5.2, max: 15.1,
    p50: 7.9, p95: 13.8, p99: 15.1, stddev: 3.4,
  },
  speedup_mean: 5.2,
  speedup_median: 5.3,
  improvement_pct: 420,
  winner: 'readyset',
}

const MOCK_ORIGIN_WINS: CacheRunResult = {
  success: true,
  query: 'SELECT id, name FROM categories LIMIT 10',
  iterations: 5,
  origin_stats: {
    mean: 978.0, median: 944.4, min: 850.2, max: 1120.5,
    p50: 944.4, p95: 1090.3, p99: 1120.5, stddev: 95.1,
  },
  cache_stats: {
    mean: 1340.0, median: 1320.1, min: 1180.5, max: 1540.8,
    p50: 1320.1, p95: 1510.2, p99: 1540.8, stddev: 120.3,
  },
  speedup_mean: 0.73,
  speedup_median: 0.72,
  improvement_pct: -27,
  winner: 'origin',
}

// --- Inline comparison components (copied from cache.tsx for test isolation) ---

function formatMs(ms: number): string {
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(1)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function LatencyBar({
  label, value, maxValue, variant, delay = 0,
}: {
  label: string; value: number; maxValue: number;
  variant: 'origin' | 'cache-win' | 'cache-lose'; delay?: number;
}) {
  const pct = maxValue > 0 ? Math.max((value / maxValue) * 100, 2) : 2
  const barColor =
    variant === 'cache-win' ? 'bg-surface-positive-solid'
      : variant === 'origin' ? 'bg-content-layout-3/40'
        : 'bg-surface-warning-solid/70'
  const textColor = variant === 'cache-win' ? 'text-content-positive-soft' : 'text-content-layout-1'
  return (
    <div className="flex items-center gap-3">
      <Text level="caption" className="text-content-layout-3 w-8 text-right shrink-0">{label}</Text>
      <div className="flex-1 h-6 bg-surface-layout-2/50 rounded-md overflow-hidden relative">
        <m.div
          className={`h-full rounded-md ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
      <Text level="mono-small" className={`w-16 text-right shrink-0 tabular-nums ${textColor}`}>{formatMs(value)}</Text>
    </div>
  )
}

function MockComparisonResult({ result }: { result: CacheRunResult }) {
  const isWinner = result.winner === 'readyset'
  const maxLatency = Math.max(
    result.origin_stats.mean, result.origin_stats.p50, result.origin_stats.p95,
    result.cache_stats.mean, result.cache_stats.p50, result.cache_stats.p95,
  )
  const speedupDisplay = isWinner
    ? `${result.speedup_mean.toFixed(1)}x`
    : `${Math.abs(result.improvement_pct).toFixed(0)}%`
  return (
    <m.div
      className="rounded-xl border border-border-layout-1 bg-surface-layout-1/80 overflow-hidden"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div className={`px-5 py-3 flex items-center justify-between ${
        isWinner
          ? 'bg-surface-positive-soft/15 border-b border-border-positive-soft/30'
          : 'bg-surface-warning-soft/10 border-b border-border-warning-soft/30'
      }`}>
        <HStack className="gap-3 items-center">
          <m.div
            className={`flex items-center justify-center rounded-lg px-3 py-1.5 font-mono text-sm font-semibold tracking-tight ${
              isWinner ? 'bg-surface-positive-solid text-white' : 'bg-surface-warning-solid text-white'
            }`}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3, delay: 0.2 }}
          >
            {isWinner ? <>{speedupDisplay} faster</> : <>{speedupDisplay} slower</>}
          </m.div>
          <Text level="label-small" className="text-content-layout-2">
            {isWinner ? 'ReadySet cache outperforms origin' : 'Origin is faster — cache may need warming'}
          </Text>
        </HStack>
        <button type="button" className="p-1 rounded-md hover:bg-surface-layout-2 transition-colors text-content-layout-3 hover:text-content-layout-1 cursor-pointer">
          <Icon name="close" label="Dismiss" className="w-4 h-4" />
        </button>
      </div>
      <div className="px-5 py-4 grid grid-cols-2 gap-6">
        <div className="space-y-2">
          <HStack className="gap-2 items-center mb-1">
            <div className="w-2 h-2 rounded-full bg-content-layout-3/40" />
            <Text level="overline" className="text-content-layout-3 uppercase tracking-widest text-[10px]">Origin</Text>
          </HStack>
          <LatencyBar label="Mean" value={result.origin_stats.mean} maxValue={maxLatency} variant="origin" delay={0.1} />
          <LatencyBar label="P50" value={result.origin_stats.p50} maxValue={maxLatency} variant="origin" delay={0.15} />
          <LatencyBar label="P95" value={result.origin_stats.p95} maxValue={maxLatency} variant="origin" delay={0.2} />
        </div>
        <div className="space-y-2">
          <HStack className="gap-2 items-center mb-1">
            <div className={`w-2 h-2 rounded-full ${isWinner ? 'bg-surface-positive-solid' : 'bg-surface-warning-solid/70'}`} />
            <Text level="overline" className="text-content-layout-3 uppercase tracking-widest text-[10px]">ReadySet</Text>
          </HStack>
          <LatencyBar label="Mean" value={result.cache_stats.mean} maxValue={maxLatency} variant={isWinner ? 'cache-win' : 'cache-lose'} delay={0.25} />
          <LatencyBar label="P50" value={result.cache_stats.p50} maxValue={maxLatency} variant={isWinner ? 'cache-win' : 'cache-lose'} delay={0.3} />
          <LatencyBar label="P95" value={result.cache_stats.p95} maxValue={maxLatency} variant={isWinner ? 'cache-win' : 'cache-lose'} delay={0.35} />
        </div>
      </div>
      <div className="px-5 py-2 border-t border-border-layout-1/50 flex items-center justify-between">
        <Text level="caption" className="text-content-layout-3">
          {result.iterations} iterations &middot; min {formatMs(Math.min(result.origin_stats.min, result.cache_stats.min))} &middot; max {formatMs(Math.max(result.origin_stats.max, result.cache_stats.max))}
        </Text>
        <HStack className="gap-4">
          {(['min', 'max', 'p99'] as const).map((stat) => (
            <HStack key={stat} className="gap-1.5 items-center">
              <Text level="caption" className="text-content-layout-3 uppercase text-[10px]">{stat}</Text>
              <Text level="mono-small" className="text-content-layout-2 tabular-nums text-xs">{formatMs(result.cache_stats[stat])}</Text>
            </HStack>
          ))}
        </HStack>
      </div>
    </m.div>
  )
}

// --- Mock queries ---

const SMALL_QUERY = `SELECT * FROM users WHERE id = :p1 AND name = :p2 AND age > :p3`

const LARGE_QUERY = `SELECT ( CASE WHEN orders.status_id = :p8 THEN "New" WHEN orders.status_id = :p9 THEN "Open" WHEN orders.status_id = :p10 THEN "Fulfilled" WHEN orders.status_id = :p11 THEN "Cancelled" WHEN orders.status_id = :p12 THEN "Refunded" WHEN orders.status_id = :p13 THEN "Partially Refunded" WHEN orders.status_id = :p14 THEN "Bank Paid" WHEN orders.status_id = :p15 THEN "Chargeback" WHEN orders.status_id = :p16 THEN "Chargeback Refund" WHEN orders.status_id = :p17 THEN "Refund Requested" ELSE "Open" END ) AS status, \`orders\`.\`shop_id\`, \`shops\`.\`slug\` as \`shop_slug\`, \`shops\`.\`name\` as \`shop_name\`, \`orders\`.\`id\` as \`id\`, CONCAT( orders_shipping_addresses.first_name, " ", orders_shipping_addresses.last_name ) AS customer_name, \`orders\`.\`email\` as \`customer_email\`, \`orders_payments\`.\`cc_last_four\` as \`last_4_digit\`, \`orders_payments\`.\`cc_brand\` as \`cc_flag\`, \`orders_payments\`.\`type\` as \`order_type\`, \`orders\`.\`status_id\` as \`order_status_id\`, \`orders_payments\`.\`gateway\` as \`payment_gateway\`, ( SELECT GROUP_CONCAT(slip_url) FROM orders_refunds WHERE orders_refunds.order_id = orders.id GROUP BY orders_refunds.order_id ) AS slip_urls, \`orders\`.\`created_at\`, \`orders\`.\`name\`, ( SELECT SUM(actual_price_paid) FROM orders_payments WHERE orders_payments.order_id = orders.id AND orders_payments.status_id = :p18 ) AS total_price_paid, ( SELECT GROUP_CONCAT(actual_price_paid SEPARATOR " ") FROM orders_payments WHERE orders_payments.order_id = orders.id AND orders_payments.status_id = :p19 ) AS actual_price_paids, \`orders_payments\`.\`actual_exchange_rate\`, \`orders_payments\`.\`id\` as \`order_payment_id\`, \`orders_payments\`.\`actual_price_paid\`, \`orders_payments\`.\`actual_price_paid_currency\`
FROM \`orders\`
INNER JOIN \`shops\` ON \`shops\`.\`id\` = \`orders\`.\`shop_id\`
INNER JOIN \`cartpanda_merchant_details\` ON \`cartpanda_merchant_details\`.\`shop_id\` = \`shops\`.\`id\`
LEFT JOIN \`settings_general\` ON \`settings_general\`.\`shop_id\` = \`shops\`.\`id\`
LEFT JOIN \`orders_payments\` ON \`orders_payments\`.\`order_id\` = \`orders\`.\`id\`
LEFT JOIN \`subscription_orders\` ON \`subscription_orders\`.\`order_id\` = \`orders\`.\`id\`
LEFT JOIN \`subscriptions\` ON \`subscriptions\`.\`id\` = \`subscription_orders\`.\`subscription_id\`
LEFT JOIN \`orders_shipping_addresses\` ON \`orders_shipping_addresses\`.\`order_id\` = \`orders\`.\`id\`
LEFT JOIN \`orders_line_items\` ON \`orders_line_items\`.\`order_id\` = \`orders\`.\`id\` and \`orders_line_items\`.\`is_ocu\` = :p20
LEFT JOIN \`products\` ON \`products\`.\`id\` = \`orders_line_items\`.\`product_id\`
LEFT JOIN \`phone_numbers\` ON \`phone_numbers\`.\`phone_numberable_id\` = \`orders\`.\`id\` and \`phone_numbers\`.\`phone_numberable_type\` = :p1
WHERE \`orders\`.\`created_at\` between :p2 and :p3 and \`orders_payments\`.\`gateway\` = :p4 and CONCAT( :p5, phone_numbers.isd_code, phone_numbers.phone_number )="+:p21" and ( \`orders_payments\`.\`actual_price_paid_currency\` is not null and \`orders_payments\`.\`actual_price_paid_currency\` != :p6 and \`orders_payments\`.\`actual_price_paid_currency\` != :p7 )
GROUP BY \`orders\`.\`id\`
ORDER BY \`orders\`.\`id\` asc
LIMIT :p22
OFFSET :p23`

const MEDIUM_QUERY = `SELECT count(*) as aggregate
FROM (
SELECT \`orders\`.\`id\`, \`orders\`.\`browser_ip\`, \`orders\`.\`buyer_accepts_marketing\`, \`orders\`.\`cancel_reason\`, \`orders\`.\`cancelled_at\`, \`orders\`.\`email\`, \`orders\`.\`created_at\`, \`orders\`.\`currency\`, \`orders\`.\`total_price\`
FROM \`orders\` force index (\`idx_shop_id\`)
INNER JOIN \`orders_payments\` ON \`orders\`.\`id\` = \`orders_payments\`.\`order_id\`
LEFT JOIN \`orders_discounts\` ON \`orders_discounts\`.\`order_id\` = \`orders\`.\`id\`
WHERE ( \`orders\`.\`shop_id\` = :p1 and \`orders\`.\`test\` = :p2 ) and \`orders\`.\`id\` > :p3 and \`orders\`.\`created_at\` >= :p4 and \`orders\`.\`updated_at\` >= :p5
GROUP BY \`orders\`.\`id\` ) as \`aggregate_table\``

const LARGE_QUERY_INITIAL_VALUES: Record<string, string | number> = {
  p1: 'Order',
  p2: '2024-01-01',
  p3: '2025-01-01',
  p4: 'cartpanda_pay',
  p5: '+',
  p6: '',
  p7: 'BRL',
  p8: 1,
  p9: 2,
  p10: 3,
  p11: 4,
  p12: 5,
  p13: 6,
  p14: 7,
  p15: 8,
  p16: 9,
  p17: 10,
  p18: 2,
  p19: 2,
  p20: 0,
  p21: '5511999999999',
  p22: 10,
  p23: 0,
}

function TestPage() {
  const [openDialog, setOpenDialog] = useState<string | null>(null)
  const [lastSubmitted, setLastSubmitted] = useState<string>('')

  return (
    <div className="p-8 max-w-4xl mx-auto space-y-8">
      <div>
        <Text as="h1" level="headline-3" className="text-content-layout-1">
          Component Test Page
        </Text>
        <Text level="body-small" className="text-content-layout-3 mt-1">
          Visual verification for ParameterDialog and ComparisonResult.
        </Text>
      </div>

      {/* ================================================================ */}
      {/* Cache Run — Comparison Results                                    */}
      {/* ================================================================ */}
      <div className="space-y-4">
        <Text level="headline-4" className="text-content-layout-1">
          Cache Run — Comparison Results
        </Text>

        <div className="space-y-3">
          <Text level="label-small" className="text-content-layout-2">
            Cache wins big (173.8x faster — sub-ms cache vs 142ms origin)
          </Text>
          <MockComparisonResult result={MOCK_CACHE_WINS} />
        </div>

        <div className="space-y-3">
          <Text level="label-small" className="text-content-layout-2">
            Cache wins moderate (5.2x faster)
          </Text>
          <MockComparisonResult result={MOCK_CACHE_WINS_MODERATE} />
        </div>

        <div className="space-y-3">
          <Text level="label-small" className="text-content-layout-2">
            Origin wins (cache 27% slower — needs warming)
          </Text>
          <MockComparisonResult result={MOCK_ORIGIN_WINS} />
        </div>
      </div>

      <hr className="border-border-layout-1" />

      {/* ================================================================ */}
      {/* ParameterDialog                                                   */}
      {/* ================================================================ */}
      <Text level="headline-4" className="text-content-layout-1">
        ParameterDialog
      </Text>

      <VStack gap="lg">
        {/* Small query - few params */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-base" className="text-content-layout-1">
            Small query (3 params) — should use base modal, single column
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Small Query Dialog"
            onClick={() => setOpenDialog('small')}
          />
          <ParameterDialog
            isOpen={openDialog === 'small'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(sql) => {
              setLastSubmitted(sql)
              setOpenDialog(null)
            }}
            query={SMALL_QUERY}
          />
        </div>

        {/* Medium query - 5 params */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-base" className="text-content-layout-1">
            Medium query (5 params) — should use base modal, single column
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Medium Query Dialog"
            onClick={() => setOpenDialog('medium')}
          />
          <ParameterDialog
            isOpen={openDialog === 'medium'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(sql) => {
              setLastSubmitted(sql)
              setOpenDialog(null)
            }}
            query={MEDIUM_QUERY}
          />
        </div>

        {/* Large query - 23 params */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-base" className="text-content-layout-1">
            Large query (23 params) — should use extra-large modal, 2-column grid, expand/collapse
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Large Query Dialog"
            onClick={() => setOpenDialog('large')}
          />
          <ParameterDialog
            isOpen={openDialog === 'large'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(sql) => {
              setLastSubmitted(sql)
              setOpenDialog(null)
            }}
            query={LARGE_QUERY}
            initialValues={LARGE_QUERY_INITIAL_VALUES}
          />
        </div>

        {/* Large query with initial values pre-filled */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-base" className="text-content-layout-1">
            Large query with pre-filled values — verify initial values populate
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Pre-filled Dialog"
            onClick={() => setOpenDialog('prefilled')}
          />
          <ParameterDialog
            isOpen={openDialog === 'prefilled'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(sql) => {
              setLastSubmitted(sql)
              setOpenDialog(null)
            }}
            query={LARGE_QUERY}
            initialValues={LARGE_QUERY_INITIAL_VALUES}
          />
        </div>

        {/* Custom submit label */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-small" className="text-content-layout-1">
            Custom submit label — "Run Comparison" with play icon
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Custom Label Dialog"
            onClick={() => setOpenDialog('custom-label')}
          />
          <ParameterDialog
            isOpen={openDialog === 'custom-label'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(sql) => {
              setLastSubmitted(sql)
              setOpenDialog(null)
            }}
            query={MEDIUM_QUERY}
            submitLabel="Run Comparison"
            submitIcon="play"
          />
        </div>
      </VStack>

      {/* Last submitted output */}
      {lastSubmitted && (
        <div className="space-y-2">
          <Text level="label-small" className="text-content-layout-3 uppercase tracking-wider">
            Last Submitted Query
          </Text>
          <pre className="bg-surface-layout-1 border border-border-layout-1 rounded-lg p-4 text-sm overflow-auto max-h-64 whitespace-pre-wrap">
            {lastSubmitted}
          </pre>
        </div>
      )}
    </div>
  )
}
