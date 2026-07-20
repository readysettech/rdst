import { Text } from '@rs/ui-new/text'
import { Icon } from '@rs/ui-new/icon'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Progress } from '@rs/ui-new/progress'
import { HStack, VStack } from '@rs/ui-new/stack'
import type { SchemaDetails, SchemaStatus } from '../../types/schema'

interface SchemaReadinessBandProps {
  schema: SchemaDetails
  status: SchemaStatus
  onAnnotate: () => void
  annotating?: boolean
  /** Live "Annotating posts… (4/7)" label while a run is in flight. */
  annotateLabel?: string | null
  disabled?: boolean
}

// A table/column counts as "documented" once it carries a non-empty
// description. The figure is derived from the already-loaded schema payload —
// it re-presents existing annotation state, it is not a new data source.
const hasText = (value?: string | null): boolean => !!value && value.trim().length > 0

// At/above this share of documented tables+columns the screen is treated as
// well-documented: the prominent band collapses to one calm line. Presentational
// threshold only.
const WELL_DOCUMENTED_PCT = 80

/**
 * Region B — documentation-readiness band (redesign §Target state). Turns the
 * vague "populated but blank" state into a self-evident "do this next": a rising
 * meter, what still needs meanings, and the one AI action that fills it. Once
 * well-documented it collapses to a single quiet line so the screen goes calm.
 */
export function SchemaReadinessBand({
  schema,
  status,
  onAnnotate,
  annotating,
  annotateLabel,
  disabled,
}: SchemaReadinessBandProps) {
  const totalTables = schema.tables.length
  const totalColumns = schema.tables.reduce((sum, t) => sum + t.columns.length, 0)
  const documentedTables = schema.tables.filter((t) => hasText(t.description)).length
  const documentedColumns = schema.tables.reduce(
    (sum, t) => sum + t.columns.filter((c) => hasText(c.description)).length,
    0,
  )

  const totalUnits = totalTables + totalColumns
  const documentedUnits = documentedTables + documentedColumns
  const pct = totalUnits > 0 ? Math.round((documentedUnits / totalUnits) * 100) : 0

  const undocumentedTables = totalTables - documentedTables
  const undocumentedColumns = totalColumns - documentedColumns

  const wellDocumented = pct >= WELL_DOCUMENTED_PCT
  const updatedLabel = status.updated_at
    ? new Date(status.updated_at).toLocaleDateString()
    : null

  // Well-documented: one quiet line, no card chrome — the job is done.
  if (wellDocumented) {
    return (
      <HStack className="gap-2 items-center px-1 flex-wrap">
        <Icon name="tick" label="Documented" className="w-4 h-4 text-content-positive-soft" />
        <Text level="body-small" className="text-content-layout-3">
          AI-documented
          {updatedLabel ? ` · Updated ${updatedLabel}` : ''} · {totalTables} tables ·{' '}
          {totalColumns} columns
        </Text>
      </HStack>
    )
  }

  // Under-documented: the prominent, elevation-lifted "do this next" band.
  return (
    <Card className="w-full border-transparent bg-surface-raised shadow-small">
      <Card.Content className="py-5">
        <HStack className="justify-between items-center flex-wrap gap-6">
          <VStack className="gap-2 items-start min-w-0">
            <HStack className="gap-3 items-center flex-wrap">
              <Progress value={pct} max={100} />
              <Text level="label-medium" className="text-content-layout-1 tabular-nums">
                {pct}% documented
              </Text>
            </HStack>
            <Text level="body-small" className="text-content-layout-2">
              {undocumentedTables} of {totalTables} tables and {undocumentedColumns} of{' '}
              {totalColumns} columns still need meanings.
            </Text>
            <Text level="caption" className="text-content-layout-3">
              Let AI describe them in about a minute.
            </Text>
          </VStack>
          <Button
            variant="rising"
            modifier="solid"
            icon="sparkles"
            iconPosition="left"
            label={annotateLabel || 'Annotate with AI'}
            onClick={onAnnotate}
            loading={annotating}
            disabled={disabled}
          />
        </HStack>
      </Card.Content>
    </Card>
  )
}
