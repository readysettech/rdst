import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { JevAssessmentSummary } from '../../../lib/api'
import { formatTimestamp } from '../../../lib/formatters'
import { schemaBasis } from '../../database/queries/jev'
import {
  QUERY_LIBRARY_FINDING_DEFINITIONS,
  QUERY_LIBRARY_FINDING_LABELS,
} from '../library/queryLibrarySelectors'
import type { QueryLibraryFindingFilter } from '../library/queryLibraryState'
import { InfoTip } from './InfoTip'

const JEV_TAG_DEFINITION =
  "RDST reads the SQL and schema to flag structural risks. It doesn't run the query. Deep Analyze measures the real plan."
const JEV_PANEL_INTRO =
  'The classification comes from the SQL and schema, before deep analysis. RDST flags structural risks without running the query.'
const JEV_PANEL_FOOTER = 'Run a deep analysis for measured facts.'
const JEV_PANEL_FOOTER_ANALYZED =
  'The deep analysis has the measured facts. See its results below.'

const STATUS_COPY: Record<string, string> = {
  pending: 'Waiting for query classification',
  running: 'RDST is classifying this query',
  waiting_connection: 'Classification waiting for database connection',
  waiting_schema: 'Classification waiting for schema context',
  waiting_auth: 'Sign in to Readyset for query classification',
  retry_wait: 'Classification temporarily unavailable',
  paused: 'Classification temporarily unavailable',
  unsupported: "RDST can't classify this query",
}

/**
 * Band drives the colour so the same severity reads the same way on the
 * summary and on every chip. Tag variants carry their own light and dark
 * values, so no theme-specific class is needed here.
 */
type TagVariant = 'warning' | 'informative' | 'neutral' | 'muted' | 'negative'

const BAND_VARIANT: Record<string, TagVariant> = {
  High: 'warning',
  Medium: 'informative',
  Low: 'neutral',
  Limited: 'muted',
}

function bandVariant(band?: string): TagVariant {
  return BAND_VARIANT[band ?? ''] ?? 'neutral'
}

/** A strong concern outranks the band; a possible one follows it. */
function findingVariant(verdict: string, band?: string): TagVariant {
  if (verdict === 'strong_concern') return 'negative'
  return bandVariant(band)
}

/** Jev's verdict sets the wording: a strong concern is stated, a possible one is hedged. */
function findingLabel(id: string, fallback: string, verdict?: string) {
  const label =
    QUERY_LIBRARY_FINDING_LABELS[id as QueryLibraryFindingFilter] ?? fallback
  if (verdict === 'strong_concern' || !label) return label
  return `Possible ${label.charAt(0).toLowerCase()}${label.slice(1)}`
}

export function JevAssessment({
  assessment,
  onDeepAnalyze,
  hasDeepAnalysis,
}: {
  assessment?: JevAssessmentSummary | null
  onDeepAnalyze: () => void
  hasDeepAnalysis: boolean
}) {
  const [open, setOpen] = useState(false)
  const status = assessment?.status ?? 'pending'
  const complete = status === 'complete'
  const limited = complete && assessment?.band === 'Limited'
  const findings = assessment?.findings ?? []
  const basis = schemaBasis(assessment?.schema_coverage)
  const summary = complete
    ? limited
      ? 'Limited classification'
      : `${assessment?.band || 'Unranked'} priority`
    : STATUS_COPY[status] || 'Classification temporarily unavailable'

  return (
    <VStack className="items-stretch gap-2" data-testid="jev-assessment">
      {/* One row, laid out like the deep-analysis row: state on the left, the
          action on the right, so the two read as a pair rather than as two
          competing strips. */}
      <HStack className="min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <HStack className="min-w-0 flex-wrap items-center gap-2">
          {complete ? (
            <span className="inline-flex items-center">
              <Tag
                size="small"
                variant={bandVariant(assessment?.band)}
                modifier="solid"
                label={summary}
              />
              <InfoTip text={JEV_TAG_DEFINITION} />
            </span>
          ) : (
            <Text level="body-small" className="text-content-layout-2">
              {summary}
            </Text>
          )}
          {/* The concerns are why the card ranks where it does, so they stay
              readable without opening the panel. */}
          {findings.map((finding) => (
            <span key={finding.id} className="inline-flex items-center">
              <Tag
                size="small"
                variant={findingVariant(
                  finding.verdict ?? '',
                  assessment?.band
                )}
                modifier="ghost"
                label={findingLabel(
                  finding.id ?? '',
                  finding.label ?? '',
                  finding.verdict
                )}
              />
              <InfoTip
                text={
                  QUERY_LIBRARY_FINDING_DEFINITIONS[finding.id ?? ''] ??
                  finding.description
                }
              />
            </span>
          ))}
        </HStack>
        {complete ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon={open ? 'chevron-up' : 'chevron-down'}
            iconPosition="right"
            label={
              open
                ? 'Hide classification details'
                : 'View classification details'
            }
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            className="shrink-0"
          />
        ) : null}
      </HStack>

      {complete && open ? (
        <VStack className="w-full items-start gap-3 rounded border border-border-layout-1 bg-surface-layout-1 px-4 py-3">
          <Text level="body-small" className="text-content-layout-2">
            {limited
              ? "RDST couldn't classify this query from the available schema."
              : JEV_PANEL_INTRO}
          </Text>
          {findings.map((finding) => (
            <div key={finding.id}>
              <Text level="label-small" className="text-content-layout-1">
                {findingLabel(
                  finding.id ?? '',
                  finding.label ?? '',
                  finding.verdict
                )}
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                {QUERY_LIBRARY_FINDING_DEFINITIONS[finding.id ?? ''] ??
                  finding.description}
              </Text>
            </div>
          ))}
          {findings.length === 0 && !limited ? (
            <Text level="body-small" className="text-content-layout-2">
              No structural risk found.
            </Text>
          ) : null}
          {hasDeepAnalysis ? (
            <Alert
              variant="informative"
              modifier="outline"
              icon="info"
              iconPosition="left"
              label={JEV_PANEL_FOOTER_ANALYZED}
            />
          ) : (
            <Text level="body-small" className="text-content-layout-2">
              {JEV_PANEL_FOOTER}
            </Text>
          )}
          {basis || assessment?.assessed_at ? (
            <Text
              level="caption"
              className="text-content-layout-3"
              data-testid="jev-basis"
            >
              {[
                basis,
                basis && assessment?.schema_collected_at
                  ? `schema read ${formatTimestamp(assessment.schema_collected_at).toLowerCase()}`
                  : null,
                assessment?.assessed_at
                  ? `classified ${formatTimestamp(assessment.assessed_at).toLowerCase()}`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </Text>
          ) : null}
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            icon="speedometer"
            iconPosition="left"
            label={
              hasDeepAnalysis ? 'View deep analysis' : 'Deep analyze this query'
            }
            onClick={onDeepAnalyze}
          />
        </VStack>
      ) : null}
    </VStack>
  )
}
