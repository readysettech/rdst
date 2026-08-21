/**
 * Design-lab prototype: the Benchmarks setup layout rendered with the
 * canonical selectable QueryCard instead of PerformanceQueryRow. Unselected
 * cards collapse to a one-line SQL preview; selecting a card formats its SQL
 * and, when it has parameters, hosts the input grid inside the card body.
 * Fixture-driven and DEV-only in spirit: the page is reachable only by its
 * unlisted /lab URL, exactly like the Queries design lab, and never talks to
 * a backend.
 */

import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card-2'
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { WorkspaceLayout } from '../../../components/workspace/WorkspaceLayout'
import { formatMeta, shortHash } from '../../../lib/formatters'
import { detectParameters, type Parameter } from '../../../lib/sqlParameters'
import { CompareSummaryRow } from '../compare/compareUi'
import {
  PerformanceCacheabilityNote,
  PerformanceQueryCard,
  PerformanceQueryParameters,
  performanceParameterKey,
} from '../shared/PerformanceQueryList'
import { SuggestValuesPanel } from '../shared/SuggestValuesPanel'

interface LabFixture {
  hash: string
  title: string
  sql: string
  readysetSupported?: string
  checkedAt?: string
  parameters: Parameter[]
}

const NOT_CACHEABLE_CHECKED_AT = new Date(
  Date.now() - 12 * 60_000
).toISOString()

function fixture(raw: Omit<LabFixture, 'parameters'>): LabFixture {
  return { ...raw, parameters: detectParameters(raw.sql) }
}

const FIXTURES: LabFixture[] = [
  fixture({
    hash: 'f1a2b3c4d5e6f708',
    title: 'Orders by status',
    sql: 'SELECT o.id, o.email, o.total_price\nFROM orders o\nWHERE o.status = :status\n  AND o.created_at >= :since\nORDER BY o.created_at DESC\nLIMIT 50',
  }),
  fixture({
    hash: 'a90b81c72d63e547',
    title: 'Categories list',
    sql: 'SELECT id, name FROM categories ORDER BY name LIMIT 20',
  }),
  fixture({
    hash: 'c477d3661e2559f0',
    title: 'Daily revenue rollup',
    sql: "SELECT s.name AS shop_name,\n  DATE_TRUNC('day', o.created_at) AS day,\n  COUNT(*) AS orders,\n  SUM(p.actual_price_paid) AS revenue\nFROM orders o\nINNER JOIN shops s ON s.id = o.shop_id\nLEFT JOIN orders_payments p ON p.order_id = o.id\nLEFT JOIN orders_discounts d ON d.order_id = o.id\nWHERE o.test = false\n  AND o.cancelled_at IS NULL\nGROUP BY s.name, DATE_TRUNC('day', o.created_at)\nORDER BY day DESC",
  }),
  fixture({
    hash: 'e58f4a3b2c1d0e9f',
    title: 'Latest signup per email',
    sql: 'SELECT DISTINCT ON (u.email) u.id, u.email, u.created_at\nFROM users u\nORDER BY u.email, u.created_at DESC',
    readysetSupported: 'unsupported: unsupported query',
    checkedAt: NOT_CACHEABLE_CHECKED_AT,
  }),
  fixture({
    hash: 'b3c6d9e2f5081a4b',
    title: 'Low-stock variants',
    sql: 'SELECT v.sku, v.title, i.available\nFROM product_variants v\nJOIN inventory_levels i ON i.variant_id = v.id\nWHERE i.available < 5\n  AND v.tracked = true\nORDER BY i.available ASC',
  }),
]

const FIXTURE_SUGGESTIONS: Record<string, string> = {
  ':status': 'fulfilled',
  ':since': '2026-07-01',
}

function fixtureMeta(entry: LabFixture) {
  return (
    <>
      {formatMeta([`hash ${shortHash(entry.hash)}`, 'demo'])}
      <PerformanceCacheabilityNote
        readysetSupported={entry.readysetSupported}
        checkedAt={entry.checkedAt}
      />
    </>
  )
}

export function PerformanceCardsLabPage() {
  const navigate = useNavigate()
  const [selectedIds, setSelectedIds] = useState<string[]>([FIXTURES[0].hash])
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [paramSources, setParamSources] = useState<Record<string, string>>({})
  const [suggesting, setSuggesting] = useState(false)
  const [suggestionMessage, setSuggestionMessage] = useState<string | null>(
    null
  )

  const selectedFixtures = FIXTURES.filter((entry) =>
    selectedIds.includes(entry.hash)
  )
  const selectedParameters = selectedFixtures.flatMap((entry) =>
    entry.parameters.map((parameter) => ({
      key: performanceParameterKey(entry.hash, parameter),
      parameter,
    }))
  )
  const missingParameterCount = selectedParameters.filter(
    ({ key }) => !paramValues[key]?.trim()
  ).length
  const parameterCount = selectedParameters.length
  const allSelected = selectedIds.length === FIXTURES.length

  const toggle = (hash: string) =>
    setSelectedIds((current) =>
      current.includes(hash)
        ? current.filter((id) => id !== hash)
        : [...current, hash]
    )

  const suggestValues = () => {
    setSuggesting(true)
    window.setTimeout(() => {
      let filled = 0
      setParamValues((values) => {
        const next = { ...values }
        const sources: Record<string, string> = {}
        for (const { key, parameter } of selectedParameters) {
          if (next[key]?.trim()) continue
          next[key] =
            FIXTURE_SUGGESTIONS[parameter.placeholder] ?? 'sample-value'
          sources[key] = 'Suggested from fixture schema evidence'
          filled += 1
        }
        setParamSources((current) => ({ ...current, ...sources }))
        return next
      })
      setSuggestionMessage(
        filled > 0
          ? `Suggested ${filled} ${filled === 1 ? 'value' : 'values'} from fixture schema evidence.`
          : 'Every parameter already has a value.'
      )
      setSuggesting(false)
    }, 450)
  }

  const readiness =
    parameterCount === 0
      ? 'No parameters'
      : missingParameterCount === 0
        ? `${parameterCount} ready`
        : `${missingParameterCount} missing`

  return (
    <WorkspaceLayout
      title="Performance cards"
      description="Design prototype: the Benchmarks setup rendered with the canonical selectable QueryCard. Fixture data only — nothing on this page runs against a database."
      icon="test-tube"
      panelId="performance-cards-lab"
      titleMeta={
        <Tag
          size="base"
          variant="warning"
          modifier="ghost"
          label="Design prototype"
        />
      }
      headerActions={
        <Button
          variant="primary"
          modifier="ghost"
          label="Back to Benchmarks"
          icon="arrow-left"
          iconPosition="left"
          onClick={() => void navigate({ to: '/cache' })}
        />
      }
    >
      <Card>
        <Card.Header className="items-start gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
          <HStack className="min-w-0 items-center gap-3">
            <IconTile icon="play" size="base" accent="primary" />
            <VStack className="min-w-0 items-start gap-0.5">
              <Card.Title>Compare cache performance</Card.Title>
              <Card.Description>
                Apply the same concurrency to upstream and Readyset.
              </Card.Description>
            </VStack>
          </HStack>
        </Card.Header>

        <Card.Content>
          <div className="grid gap-8 tablet:grid-cols-3">
            <VStack className="min-w-0 items-stretch gap-3 tablet:col-span-2">
              <HStack className="flex-wrap items-center justify-between gap-4">
                <VStack className="items-start gap-0.5">
                  <Text level="label-small" className="text-content-layout-1">
                    Queries
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    Each row is the canonical QueryCard selectable variant.
                    Unselected rows collapse to one line; selecting formats the
                    SQL.
                  </Text>
                </VStack>
                <Button
                  size="small"
                  variant="primary"
                  modifier="ghost"
                  label={allSelected ? 'Clear all' : 'Select all'}
                  onClick={() =>
                    setSelectedIds(
                      allSelected ? [] : FIXTURES.map((entry) => entry.hash)
                    )
                  }
                />
              </HStack>

              <VStack
                className="items-stretch gap-3"
                aria-label="Queries available for comparison"
              >
                {FIXTURES.map((entry) => {
                  const selected = selectedIds.includes(entry.hash)
                  return (
                    <PerformanceQueryCard
                      key={entry.hash}
                      queryHash={entry.hash}
                      sql={entry.sql}
                      title={entry.title}
                      meta={fixtureMeta(entry)}
                      selected={selected}
                      onSelect={() => toggle(entry.hash)}
                      parameterCount={entry.parameters.length}
                      parameterContent={
                        entry.parameters.length > 0 ? (
                          <PerformanceQueryParameters
                            ownerId={entry.hash}
                            inputPrefix="lab"
                            parameters={entry.parameters}
                            values={paramValues}
                            sources={paramSources}
                            onValueChange={(parameter, value) =>
                              setParamValues((current) => ({
                                ...current,
                                [performanceParameterKey(
                                  entry.hash,
                                  parameter
                                )]: value,
                              }))
                            }
                          />
                        ) : undefined
                      }
                    />
                  )
                })}
              </VStack>
            </VStack>

            <VStack className="items-stretch gap-3">
              <VStack className="items-start gap-0.5">
                <Text level="label-small" className="text-content-layout-1">
                  Run summary
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  What this comparison will execute.
                </Text>
              </VStack>
              <div className="rounded-xl border border-border-layout-soft px-4">
                <CompareSummaryRow label="Database" value="demo (fixture)" />
                <CompareSummaryRow
                  label="Selected queries"
                  value={`${selectedIds.length} selected`}
                />
                <CompareSummaryRow
                  label="Load"
                  value="Safe · 2 → 4 clients per lane"
                />
                <CompareSummaryRow label="Duration" value="30 seconds" />
                <CompareSummaryRow
                  label="Parameter readiness"
                  value={readiness}
                  tone={missingParameterCount > 0 ? 'warning' : 'positive'}
                />
              </div>
              <SuggestValuesPanel
                hasParameters={parameterCount > 0}
                suggesting={suggesting}
                missingParameterCount={missingParameterCount}
                message={suggestionMessage}
                schemaUnavailable={false}
                onSuggest={suggestValues}
              />
            </VStack>
          </div>
        </Card.Content>

        <Card.Footer className="flex-wrap justify-between">
          <Text level="caption" className="mr-auto text-content-layout-3">
            Prototype only — the run action is inert.
          </Text>
          <Button
            variant="rising"
            modifier="solid"
            label="Run comparison"
            icon="play"
            disabled
          />
        </Card.Footer>
      </Card>
    </WorkspaceLayout>
  )
}
