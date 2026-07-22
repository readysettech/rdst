import type { ComponentProps, ReactNode } from 'react'
import { cn } from '@rs/tailwind-base'
import { Text } from '@rs/ui-new/text'
import { Icon } from '@rs/ui-new/icon'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Progress } from '@rs/ui-new/progress'
import { Tag } from '@rs/ui-new/tag'
import { HStack, VStack } from '@rs/ui-new/stack'
import type { SchemaDetails, SchemaStatus } from '../../types/schema'

interface SchemaGuidedSequenceProps {
  schema: SchemaDetails
  status: SchemaStatus
  onRefresh: () => void
  onProfile: () => void
  onAnnotate: () => void
  refreshing?: boolean
  profiling?: boolean
  annotating?: boolean
  /** Live "Annotating posts… (4/7)" label while an AI run is in flight. */
  annotateLabel?: string | null
  /** No usable Anthropic key (missing or rdst-0yy.7 rejection): the AI stage
   *  degrades to a warning chip instead of a live action, and the routing CTA
   *  is carried by the RoutableNotice rendered above this card. */
  annotateBlocked?: boolean
  annotateBlockedLabel?: string
  /** Disable actions while any op is in flight or the target is locked. */
  disabled?: boolean
}

// A table/column counts as "documented" once it carries a non-empty
// description — derived from the already-loaded schema, not a new data source.
const hasText = (value?: string | null): boolean => !!value && value.trim().length > 0

// At/above this share of documented tables+columns the discovery job reads as
// done and the whole sequence collapses to one calm line. Presentational only.
const WELL_DOCUMENTED_PCT = 80

function coverage(schema: SchemaDetails) {
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
  return {
    totalTables,
    totalColumns,
    undocumentedTables: totalTables - documentedTables,
    undocumentedColumns: totalColumns - documentedColumns,
    pct,
  }
}

interface StageRowProps {
  index: number
  done?: boolean
  title: string
  what: string
  cost: string
  action: ReactNode
  children?: ReactNode
}

// One staged step: a done/numbered marker, what it does, what it costs, its
// action, and (via children) any stage-specific body — a meter or a caption.
function StageRow({ index, done, title, what, cost, action, children }: StageRowProps) {
  return (
    <HStack className="gap-4 items-start">
      <div
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-label-small tabular-nums',
          done
            ? 'bg-surface-positive-soft text-content-positive-soft'
            : 'bg-surface-layout-2 text-content-layout-2',
        )}
      >
        {done ? <Icon name="tick" label="Done" className="h-4 w-4" /> : index}
      </div>
      <VStack className="min-w-0 flex-1 gap-1.5 items-start">
        <HStack className="w-full justify-between items-center gap-3 flex-wrap">
          <Text level="label-medium" className="text-content-layout-1">
            {title}
          </Text>
          <HStack className="gap-3 items-center shrink-0">
            <Text level="caption" className="hidden text-content-layout-3 tablet:block">
              {cost}
            </Text>
            {action}
          </HStack>
        </HStack>
        <Text level="body-small" className="text-content-layout-2">
          {what}
        </Text>
        {children}
      </VStack>
    </HStack>
  )
}

/**
 * Region B — the discovery pipeline as a guided sequence, not a toolbar
 * (rdst-dma.7 req 1/2). Structure -> Column profile -> AI descriptions, each
 * with what it does, why it sharpens Ask, its cost, and done/not-done state.
 * Echoes Home state 2's honesty grammar. Collapses to one calm line once the
 * schema is well-documented — the steady state where Manage handles re-runs.
 */
export function SchemaGuidedSequence({
  schema,
  status,
  onRefresh,
  onProfile,
  onAnnotate,
  refreshing,
  profiling,
  annotating,
  annotateLabel,
  annotateBlocked,
  annotateBlockedLabel,
  disabled,
}: SchemaGuidedSequenceProps) {
  const { totalTables, totalColumns, undocumentedTables, undocumentedColumns, pct } =
    coverage(schema)
  const updatedLabel = status.updated_at ? new Date(status.updated_at).toLocaleDateString() : null
  const profiledTables = status.profiled_tables ?? 0
  // The > 0 guard covers the empty-schema case, where 0 >= 0 would tick.
  const profileDone = profiledTables > 0 && profiledTables >= status.tables
  const profiledLabel = status.profiled_at
    ? new Date(status.profiled_at).toLocaleDateString()
    : null

  if (pct >= WELL_DOCUMENTED_PCT) {
    return (
      <HStack className="gap-2 items-center px-1 flex-wrap">
        <Icon name="tick" label="Documented" className="h-4 w-4 text-content-positive-soft" />
        <Text level="body-small" className="text-content-layout-3">
          AI-documented{updatedLabel ? ` · Updated ${updatedLabel}` : ''} · {totalTables} tables ·{' '}
          {totalColumns} columns
        </Text>
      </HStack>
    )
  }

  type IconName = NonNullable<ComponentProps<typeof Button>['icon']>
  const ghost = (label: string, icon: IconName, onClick: () => void, loading?: boolean) => (
    <Button
      variant="primary"
      modifier="ghost"
      size="small"
      icon={icon}
      iconPosition="left"
      label={label}
      onClick={onClick}
      loading={loading}
      disabled={disabled}
    />
  )

  const annotateAction = annotateBlocked ? (
    <Tag size="small" variant="warning" label={annotateBlockedLabel || 'Needs a working key'} />
  ) : (
    <Button
      variant="rising"
      modifier="solid"
      size="small"
      icon="sparkles"
      iconPosition="left"
      label={annotateLabel || 'Annotate with AI'}
      onClick={onAnnotate}
      loading={annotating}
      disabled={disabled}
    />
  )

  return (
    <Card className="w-full border-transparent bg-surface-raised shadow-small">
      <Card.Content className="py-5">
        <VStack className="gap-1 items-start">
          <Text level="label-large" className="text-content-layout-1">
            Make Ask precise about {status.target}
          </Text>
          <Text level="body-small" className="text-content-layout-3">
            Three staged steps — each sharpens Ask's answers. Run them top to bottom.
          </Text>
        </VStack>

        <VStack className="mt-5 gap-5 items-stretch">
          <StageRow
            index={1}
            done
            title="Structure"
            what="Tables, columns, and types, read from your database."
            cost="Free · seconds"
            action={ghost('Refresh', 'database-settings', onRefresh, refreshing)}
          >
            <Text level="caption" className="text-content-layout-3">
              Loaded{updatedLabel ? ` · updated ${updatedLabel}` : ''} · {status.tables} tables ·{' '}
              {status.columns} columns
            </Text>
          </StageRow>

          <div className="border-t border-border-layout-1" />

          <StageRow
            index={2}
            done={profileDone}
            title="Column profile"
            what="Shapes, ranges, null rates, and sample values, sampled with read-only queries."
            cost="Read-only DB · no AI"
            action={ghost('Profile', 'speedometer', onProfile, profiling)}
          >
            <Text level="caption" className="text-content-layout-3">
              {profiledTables > 0
                ? `Profiled · ${profiledTables} of ${status.tables} tables${profiledLabel ? ` · ${profiledLabel}` : ''}`
                : "Lets Ask reason about what's in a column, not just its name."}
            </Text>
          </StageRow>

          <div className="border-t border-border-layout-1" />

          <StageRow
            index={3}
            title="AI descriptions & terminology"
            what="Descriptions, business context, and terminology, written by AI."
            cost="~1 min · uses your key"
            action={annotateAction}
          >
            {/* Label first so the percentage anchors to the left margin, in line
                with the "still need meanings" copy below it — the bar trails to
                its right instead of shoving the label into the middle (the
                off-balance "0% documented" the 240px w-60 track caused). */}
            <HStack className="mt-0.5 gap-3 items-center flex-wrap">
              <Text level="label-medium" className="text-content-layout-1 tabular-nums">
                {pct}% documented
              </Text>
              <Progress value={pct} max={100} />
            </HStack>
            <Text level="body-small" className="text-content-layout-2">
              {undocumentedTables} of {totalTables} tables and {undocumentedColumns} of{' '}
              {totalColumns} columns still need meanings.
            </Text>
            <Text level="caption" className="text-content-layout-3">
              The difference between AI that guesses at your tables and AI that asks the right
              questions back.
            </Text>
          </StageRow>
        </VStack>
      </Card.Content>
    </Card>
  )
}
