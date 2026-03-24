/**
 * Test page for visually verifying ParameterDialog improvements.
 * DEV only — tree-shaken in production builds.
 */

import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { VStack } from '@rs/ui-new/stack'
import { ParameterDialog } from '../components/top/ParameterDialog'
import { BenchmarkParameterDialog } from '../components/BenchmarkParameterDialog'

export const Route = createFileRoute('/test')({
  component: TestPage,
})

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
          ParameterDialog Test Page
        </Text>
        <Text level="body-base" className="text-content-layout-3 mt-1">
          Visual verification for large query / many parameter improvements.
        </Text>
      </div>

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

        {/* Benchmark dialog */}
        <div className="border border-border-layout-1 rounded-lg p-4 space-y-3">
          <Text level="label-base" className="text-content-layout-1">
            BenchmarkParameterDialog — multiple queries with mixed param counts
          </Text>
          <Button
            variant="primary"
            modifier="solid"
            label="Open Benchmark Dialog"
            onClick={() => setOpenDialog('benchmark')}
          />
          <BenchmarkParameterDialog
            isOpen={openDialog === 'benchmark'}
            onClose={() => setOpenDialog(null)}
            onSubmit={(results) => {
              setLastSubmitted(results.map((r) => r.sql).join('\n---\n'))
              setOpenDialog(null)
            }}
            queries={[
              {
                identifier: 'abc12345',
                sql: MEDIUM_QUERY,
                name: 'Order Count Query',
                mostRecentParams: { p1: 759714, p2: 0, p3: 0, p4: '2025-01-01', p5: '2025-01-01' },
              },
              {
                identifier: 'def67890',
                sql: LARGE_QUERY,
                name: 'Order Details Query',
                mostRecentParams: LARGE_QUERY_INITIAL_VALUES,
              },
            ]}
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
