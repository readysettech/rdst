import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { TableHeaderCell } from '../components/TableHeaderCell'
import { Card } from '@rs/ui-new/card'
import {
  Drawer,
  DrawerContent,
  DrawerContentContainer,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@rs/ui-new/drawer'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { PathPicker } from '../components/PathPicker'
import { HandRaiser } from '../components/HandRaiser'
import { formatTimestamp } from '../lib/formatters'
import type {
  FleetAuditSummary,
  FleetAuditTargetState,
  FleetTargetVerdict,
} from '../lib/useFleet'
import {
  deleteFleetSnapshot,
  fetchFleetDiff,
  fetchFleetSnapshotDetail,
  fetchFleetSnapshots,
  fetchFleetTargets,
  mapSnapshotVerdicts,
  useFleetAudit,
  useFleetDiscover,
  useFleetImport,
  useFleetStatus,
} from '../lib/useFleet'
import type {
  FleetConnectivityEvent,
  FleetDiffResponse,
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
  FleetMember,
  FleetSnapshotSummary,
} from '../types/fleet'

export const Route = createFileRoute('/fleet')({
  component: FleetPage,
})

function SectionCard({
  icon,
  title,
  actions,
  children,
}: {
  icon: IconStrokeName
  title: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="justify-between items-center">
            <HStack className="gap-2 items-center">
              <Icon
                name={icon}
                label={title}
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                {title}
              </Text>
            </HStack>
            {actions}
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  )
}

const VERDICT_LABELS: Record<
  string,
  {
    label: string
    variant: 'positive' | 'warning' | 'negative' | 'informative'
  }
> = {
  right_sized: { label: 'Right-sized', variant: 'positive' },
  oversized: { label: 'Oversized', variant: 'warning' },
  under_provisioned: { label: 'Under-provisioned', variant: 'negative' },
  unknown: { label: 'Unknown', variant: 'informative' },
}

// A target "needs attention" when it can't be reached or its last sizing
// verdict is off-target (oversized / under-provisioned). Right-sized and
// not-yet-audited targets do not. Drives the scoreboard's accent count.
const PROBLEM_VERDICTS = new Set(['oversized', 'under_provisioned'])

// Proper engine display names — the backend emits lowercase enum values, which
// CSS `capitalize` renders as "Postgresql"/"Mysql". [QW19 / F8]
const ENGINE_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}
const engineLabel = (engine: string | null | undefined) =>
  engine ? (ENGINE_LABELS[engine.toLowerCase()] ?? engine) : '-'

// A dashboard stat: prominent tabular value over a subordinated label. At most
// one tile per scoreboard carries the accent (VIS-017, VIS-061, VIS-110).
function StatTile({
  value,
  label,
  accent = false,
}: {
  value: React.ReactNode
  label: string
  accent?: boolean
}) {
  return (
    <VStack className="gap-0.5 items-start">
      <Text
        as="span"
        level="headline-2"
        className={
          accent
            ? 'text-content-rising-soft tabular-nums'
            : 'text-content-layout-1 tabular-nums'
        }
      >
        {value}
      </Text>
      <Text
        as="span"
        level="caption"
        className="text-content-layout-2 uppercase tracking-wider"
      >
        {label}
      </Text>
    </VStack>
  )
}

function StatusCell({
  result,
}: {
  result: FleetConnectivityEvent | undefined
}) {
  if (!result) {
    return (
      <Text level="caption" className="text-content-layout-3">
        -
      </Text>
    )
  }
  if (result.status === 'checking') {
    return <Spinner size="base" />
  }
  if (result.status === 'ok') {
    return (
      <VStack className="gap-0.5 items-start">
        <Tag
          size="small"
          variant="positive"
          modifier="ghost"
          label={`${result.latency_ms}ms`}
        />
        {result.server_version && (
          <Text
            level="caption"
            className="text-content-layout-3 truncate max-w-48"
          >
            {result.server_version}
          </Text>
        )}
      </VStack>
    )
  }
  return (
    <VStack className="gap-0.5 items-start">
      <Tag
        size="small"
        variant="negative"
        modifier="ghost"
        label="Unreachable"
      />
      {result.error && (
        // Failure reason is the single most useful string in the row: promote
        // it out of muted grey into legible negative-content (F6, USE-100).
        <Text
          level="caption"
          className="text-content-negative-soft max-w-48"
        >
          {result.error}
        </Text>
      )}
    </VStack>
  )
}

function SizingCell({
  verdict,
}: {
  verdict: FleetTargetVerdict | undefined
}) {
  if (!verdict?.verdict) {
    // "Not audited yet" is an actionable state, not a bare dash (F11, USE-002).
    return (
      <Tag
        size="small"
        variant="muted"
        modifier="ghost"
        label="Not audited yet"
      />
    )
  }
  const info = VERDICT_LABELS[verdict.verdict] || VERDICT_LABELS.unknown
  return (
    <Tag size="small" variant={info.variant} modifier="ghost" label={info.label} />
  )
}

function MemberRow({
  member,
  status,
  verdict,
}: {
  member: FleetMember
  status: FleetConnectivityEvent | undefined
  verdict: FleetTargetVerdict | undefined
}) {
  const [expanded, setExpanded] = useState(false)
  const hasBadges = !!member.group || (member.tags?.length ?? 0) > 0
  // Engine / host / database collapse into one muted identity line so the two
  // scan-for columns (connectivity, sizing) carry the weight (VIS-110, VIS-017).
  const identity = [
    engineLabel(member.engine),
    `${member.host}:${member.port}`,
    member.database || null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <>
      <tr className="hover:bg-surface-layout-2/50 transition-colors">
        <td className="px-4 py-3">
          <VStack className="gap-1 items-start">
            <Text level="label-small" className="text-content-layout-1">
              {member.name}
            </Text>
            <Text level="caption" className="text-content-layout-2 break-all">
              {identity}
            </Text>
            {hasBadges && (
              <HStack className="gap-1 flex-wrap">
                {member.group && (
                  <Tag
                    size="small"
                    variant="primary"
                    modifier="ghost"
                    label={member.group}
                  />
                )}
                {(member.tags || []).map((tag) => (
                  <Tag
                    key={tag}
                    size="small"
                    variant="informative"
                    modifier="ghost"
                    label={tag}
                  />
                ))}
              </HStack>
            )}
          </VStack>
        </td>
        <td className="px-4 py-3 align-top">
          <StatusCell result={status} />
        </td>
        <td className="px-4 py-3 align-top">
          <SizingCell verdict={verdict} />
        </td>
        <td className="px-4 py-3 align-top text-right w-10">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon={expanded ? 'chevron-up' : 'chevron-down'}
            iconPosition="icon"
            label={expanded ? 'Hide details' : 'Show details'}
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          />
        </td>
      </tr>
      <Show when={expanded}>
        <tr className="bg-surface-layout-2/30">
          <td colSpan={4} className="px-4 py-3">
            <HStack className="gap-8 items-start flex-wrap">
              <VStack className="gap-0.5 items-start">
                <Text
                  level="caption"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Cacheability
                </Text>
                <Text level="label-small" className="text-content-layout-1">
                  {verdict?.cacheScore != null
                    ? `${verdict.cacheScore}/100`
                    : 'Not audited yet'}
                </Text>
              </VStack>
              <Show when={!!member.instance_class}>
                <VStack className="gap-0.5 items-start">
                  <Text
                    level="caption"
                    className="text-content-layout-3 uppercase tracking-wider"
                  >
                    Instance class
                  </Text>
                  <Text level="label-small" className="text-content-layout-1">
                    {member.instance_class}
                  </Text>
                </VStack>
              </Show>
            </HStack>
          </td>
        </tr>
      </Show>
    </>
  )
}

function DiffValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-content-layout-3">-</span>
  }
  if (typeof value === 'object') {
    return (
      <span className="text-content-layout-2">{JSON.stringify(value)}</span>
    )
  }
  return <span className="text-content-layout-2">{String(value)}</span>
}

function SnapshotDiff({ diff }: { diff: FleetDiffResponse }) {
  const hasChanges =
    diff.entries.length > 0 ||
    diff.new_targets.length > 0 ||
    diff.removed_targets.length > 0

  return (
    <div className="p-5 border-b border-border-layout-1 bg-surface-layout-2/30">
      <VStack className="gap-3 items-stretch">
        <HStack className="gap-2 items-center flex-wrap">
          <Text level="label-small" className="text-content-layout-1">
            Diff
          </Text>
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label={`baseline ${formatTimestamp(diff.baseline_date)}`}
          />
          <Text level="caption" className="text-content-layout-3">
            {'->'}
          </Text>
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label={`current ${formatTimestamp(diff.current_date)}`}
          />
        </HStack>

        <Show
          when={diff.new_targets.length > 0 || diff.removed_targets.length > 0}
        >
          <HStack className="gap-2 items-center flex-wrap">
            {diff.new_targets.map((name) => (
              <Tag
                key={`new-${name}`}
                size="small"
                variant="positive"
                modifier="ghost"
                label={`+ ${name}`}
              />
            ))}
            {diff.removed_targets.map((name) => (
              <Tag
                key={`removed-${name}`}
                size="small"
                variant="negative"
                modifier="ghost"
                label={`- ${name}`}
              />
            ))}
          </HStack>
        </Show>

        <Show when={diff.entries.length > 0}>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2/30">
                  {['Target', 'Field', 'Old', 'New', 'Change %'].map(
                    (header) => (
                      <TableHeaderCell key={header}>{header}</TableHeaderCell>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-layout-1">
                {diff.entries.map((entry, index) => (
                  <tr key={`${entry.target_name}-${entry.field_name}-${index}`}>
                    <td className="px-4 py-2">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        {entry.target_name}
                      </Text>
                    </td>
                    <td className="px-4 py-2">
                      <Text
                        level="mono-small"
                        className="text-content-layout-2"
                      >
                        {entry.field_name}
                      </Text>
                    </td>
                    <td className="px-4 py-2">
                      <Text level="mono-small">
                        <DiffValue value={entry.old_value} />
                      </Text>
                    </td>
                    <td className="px-4 py-2">
                      <HStack className="gap-1 items-center">
                        <Text
                          level="mono-small"
                          className="text-content-layout-3"
                        >
                          {'->'}
                        </Text>
                        <Text level="mono-small">
                          <DiffValue value={entry.new_value} />
                        </Text>
                      </HStack>
                    </td>
                    <td className="px-4 py-2">
                      {entry.change_pct === null ||
                      entry.change_pct === undefined ? (
                        <Text
                          level="mono-small"
                          className="text-content-layout-3"
                        >
                          -
                        </Text>
                      ) : (
                        // A cost/size diff is magnitude, not good/bad — neutral
                        // tag + a direction arrow, never sign-colored (§4.4, F4).
                        <Tag
                          size="small"
                          variant="neutral"
                          modifier="ghost"
                          label={`${entry.change_pct >= 0 ? '↑' : '↓'} ${Math.abs(entry.change_pct).toFixed(1)}%`}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Show>

        <Show when={!hasChanges}>
          <div className="py-6">
            <VStack className="gap-2 items-center">
              <Icon
                name="tick-double"
                label="No changes"
                className="w-6 h-6 text-content-layout-3"
              />
              <Text level="body-small" className="text-content-layout-3">
                No differences between these snapshots.
              </Text>
            </VStack>
          </div>
        </Show>
      </VStack>
    </div>
  )
}

// TERTIARY region: drift-over-time is a separate analysis job (compare row,
// snapshot list, diff table) — deferred behind a collapsed disclosure so it
// stops competing with monitoring (VIS-103, USE-021). "last audit {ts}" moves
// here from the Targets header.
function HistoryDriftSection() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['fleet-snapshots'],
    queryFn: fetchFleetSnapshots,
    staleTime: 30_000,
  })
  const snapshots = data?.snapshots || []
  const lastAuditLabel = snapshots[0]
    ? formatTimestamp(snapshots[0].created_at)
    : undefined

  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [baselineId, setBaselineId] = useState<string>('')
  const [currentId, setCurrentId] = useState<string>('')
  const [diff, setDiff] = useState<FleetDiffResponse | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (snapshotId: string) => deleteFleetSnapshot(snapshotId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fleet-snapshots'] })
      toast({ title: 'Snapshot deleted', variant: 'positive' })
    },
    onError: (err: Error) => {
      toast({
        title: 'Delete failed',
        description: err.message,
        variant: 'negative',
      })
    },
  })

  const diffMutation = useMutation({
    mutationFn: ({
      baseline,
      current,
    }: {
      baseline: string
      current: string
    }) => fetchFleetDiff(baseline, current),
    onSuccess: (result) => setDiff(result),
    onError: (err: Error) => {
      toast({
        title: 'Diff failed',
        description: err.message,
        variant: 'negative',
      })
    },
  })

  const options = snapshots.map((snapshot) => ({
    value: snapshot.snapshot_id,
    label: `${formatTimestamp(snapshot.created_at)} — ${snapshot.targets_audited} targets`,
  }))
  const canCompare = !!baselineId && !!currentId && baselineId !== currentId
  const showCompareRow = snapshots.length >= 2

  const handleDelete = (snapshotId: string) => {
    deleteMutation.mutate(snapshotId, {
      onSettled: () => setConfirmingId(null),
    })
    if (snapshotId === baselineId) setBaselineId('')
    if (snapshotId === currentId) setCurrentId('')
    setDiff(null)
  }

  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full px-5 py-3 flex items-center justify-between gap-3 text-left cursor-pointer hover:bg-surface-layout-2/50 transition-colors"
        >
          <HStack className="gap-2 items-center">
            <Icon
              name={open ? 'chevron-down' : 'chevron-right'}
              label=""
              className="w-4 h-4 text-content-layout-3"
            />
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              History &amp; drift
            </Text>
            <Text level="caption" className="text-content-layout-3">
              · {snapshots.length} snapshot{snapshots.length === 1 ? '' : 's'}
            </Text>
          </HStack>
          <Show when={!!lastAuditLabel}>
            <Text level="caption" className="text-content-layout-3">
              last audit {lastAuditLabel}
            </Text>
          </Show>
        </button>

        <Show when={open}>
          <div className="border-t border-border-layout-1">
            <Show when={showCompareRow}>
              <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
                <HStack className="gap-2 items-center flex-wrap">
                  <Text
                    level="label-small"
                    className="text-content-layout-2 shrink-0"
                  >
                    Compare:
                  </Text>
                  <div className="w-56">
                    <BaseInputSelect
                      name="snapshot-baseline"
                      placeholder="Baseline"
                      value={baselineId}
                      onValueChange={(value) => {
                        setBaselineId(value)
                        setDiff(null)
                      }}
                      options={options}
                    />
                  </div>
                  <Text
                    level="caption"
                    className="text-content-layout-3 shrink-0"
                  >
                    vs
                  </Text>
                  <div className="w-56">
                    <BaseInputSelect
                      name="snapshot-current"
                      placeholder="Current"
                      value={currentId}
                      onValueChange={(value) => {
                        setCurrentId(value)
                        setDiff(null)
                      }}
                      options={options}
                    />
                  </div>
                  {/* Outline so "Audit fleet" stays the screen's one solid
                      primary when the History & drift panel is open [S4]. */}
                  <Button
                    variant="primary"
                    modifier="outline"
                    size="small"
                    label="Compare"
                    icon="connect"
                    iconPosition="left"
                    disabled={!canCompare || diffMutation.isPending}
                    loading={diffMutation.isPending}
                    onClick={() => {
                      if (canCompare)
                        diffMutation.mutate({
                          baseline: baselineId,
                          current: currentId,
                        })
                    }}
                  />
                  <Show when={!!diff}>
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Clear"
                      onClick={() => setDiff(null)}
                    />
                  </Show>
                </HStack>
              </div>
            </Show>

            <Show when={!!diff}>
              <SnapshotDiff diff={diff!} />
            </Show>

            <Show when={isLoading}>
              <div className="p-12">
                <VStack className="gap-3 items-center">
                  <Spinner size="base" />
                  <Text level="body-small" className="text-content-layout-3">
                    Loading snapshots...
                  </Text>
                </VStack>
              </div>
            </Show>

            <Show when={!isLoading && snapshots.length === 0}>
              <div className="p-12">
                <VStack className="gap-3 items-center">
                  <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                    <Icon
                      name="package"
                      label="No snapshots"
                      className="w-7 h-7 text-content-layout-3"
                    />
                  </div>
                  <Text
                    level="body-small"
                    className="text-content-layout-3 text-center max-w-md"
                  >
                    No snapshots yet — run Audit fleet to capture drift over
                    time.
                  </Text>
                </VStack>
              </div>
            </Show>

            <Show when={!isLoading && snapshots.length > 0}>
              <div className="divide-y divide-border-layout-1">
                {snapshots.map((snapshot: FleetSnapshotSummary) => (
                  <div
                    key={snapshot.snapshot_id}
                    className="px-5 py-3 hover:bg-surface-layout-2/50 transition-colors"
                  >
                    {confirmingId === snapshot.snapshot_id ? (
                      <HStack className="justify-between items-center gap-4">
                        <HStack className="gap-2 items-center">
                          <Icon
                            name="alert"
                            label="Warning"
                            className="w-4 h-4 text-content-negative-soft"
                          />
                          <Text
                            level="body-small"
                            className="text-content-layout-1"
                          >
                            Delete snapshot "{snapshot.name}"?
                          </Text>
                        </HStack>
                        <HStack className="gap-2 shrink-0">
                          <Button
                            variant="primary"
                            modifier="ghost"
                            size="small"
                            label="Cancel"
                            onClick={() => setConfirmingId(null)}
                          />
                          <Button
                            variant="negative"
                            modifier="solid"
                            size="small"
                            label="Confirm Delete"
                            icon="trash"
                            iconPosition="left"
                            loading={deleteMutation.isPending}
                            onClick={() => handleDelete(snapshot.snapshot_id)}
                          />
                        </HStack>
                      </HStack>
                    ) : (
                      <HStack className="justify-between items-center gap-3">
                        <VStack className="gap-1 items-start min-w-0">
                          <HStack className="gap-2 items-center flex-wrap">
                            <Text
                              level="label-small"
                              className="text-content-layout-1"
                            >
                              Fleet audit — {formatTimestamp(snapshot.created_at)}
                            </Text>
                            <Tag
                              size="small"
                              variant="informative"
                              modifier="ghost"
                              label={`${snapshot.targets_audited} targets`}
                            />
                          </HStack>
                          <Text
                            level="mono-small"
                            className="text-content-layout-3 truncate"
                          >
                            {snapshot.snapshot_id}
                          </Text>
                        </VStack>
                        <Button
                          variant="negative"
                          modifier="ghost"
                          size="small"
                          icon="trash"
                          iconPosition="icon"
                          label="Delete"
                          onClick={() => setConfirmingId(snapshot.snapshot_id)}
                        />
                      </HStack>
                    )}
                  </div>
                ))}
              </div>
            </Show>
          </div>
        </Show>
      </Card.Content>
    </Card>
  )
}

function AuditTargetRow({
  name,
  state,
}: {
  name: string
  state: FleetAuditTargetState
}) {
  return (
    <div className="px-5 py-3 hover:bg-surface-layout-2/50 transition-colors">
      <HStack className="justify-between items-center gap-3">
        <Text level="label-small" className="text-content-layout-1 truncate">
          {name}
        </Text>
        <div className="shrink-0">
          <Show when={state.status === 'running'}>
            <Spinner size="base" />
          </Show>
          <Show when={state.status === 'done'}>
            <HStack className="gap-2 items-center">
              {(() => {
                const verdict =
                  VERDICT_LABELS[state.verdict || 'unknown'] ||
                  VERDICT_LABELS.unknown
                return (
                  <Tag
                    size="small"
                    variant={verdict.variant}
                    modifier="ghost"
                    label={verdict.label}
                  />
                )
              })()}
              <Text level="caption" className="text-content-layout-3">
                cache {state.cacheScore ?? '-'}/100
              </Text>
            </HStack>
          </Show>
          <Show when={state.status === 'error'}>
            <VStack className="gap-0.5 items-end">
              <Tag
                size="small"
                variant="negative"
                modifier="ghost"
                label="Failed"
              />
              {state.error && (
                // Audit failure reason in legible negative-content, not muted
                // grey (F6, USE-100).
                <Text
                  level="caption"
                  className="text-content-negative-soft max-w-64 text-right"
                >
                  {state.error}
                </Text>
              )}
            </VStack>
          </Show>
        </div>
      </HStack>
    </div>
  )
}

function FleetInsights({ summary }: { summary: FleetAuditSummary }) {
  const insights = summary.fleet_insights
  const healthScore =
    insights &&
    typeof insights === 'object' &&
    typeof insights.health_score === 'number'
      ? (insights.health_score as number)
      : undefined
  const findingsRaw =
    insights && typeof insights === 'object'
      ? (insights.top_findings ?? insights.fleet_findings)
      : undefined
  const findings = Array.isArray(findingsRaw) ? findingsRaw : []
  const hasContent = healthScore !== undefined || findings.length > 0

  if (!hasContent) {
    return (
      <Text level="caption" className="text-content-layout-3">
        Fleet insights unavailable
      </Text>
    )
  }

  const findingTitle = (f: unknown): string | undefined => {
    if (f && typeof f === 'object') {
      const rec = f as Record<string, unknown>
      const title = rec.title ?? rec.finding ?? rec.summary
      if (typeof title === 'string') return title
    }
    if (typeof f === 'string') return f
    return undefined
  }

  return (
    <VStack className="gap-1.5 items-start">
      {healthScore !== undefined && (
        <HStack className="gap-2 items-center">
          <Text
            level="caption"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            Fleet health
          </Text>
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label={`${healthScore}/100`}
          />
        </HStack>
      )}
      {findings.slice(0, 4).map((f, index) => {
        const title = findingTitle(f)
        if (!title) return null
        return (
          <HStack key={index} className="gap-2 items-start">
            <span className="mt-1.5 h-1 w-1 rounded-full bg-content-layout-3 shrink-0" />
            <Text level="caption" className="text-content-layout-2">
              {title}
            </Text>
          </HStack>
        )
      })}
    </VStack>
  )
}

function StreamLog({
  progress,
  errors,
  result,
  instancesFound,
  onClear,
}: {
  progress: FleetImportProgressEvent[]
  errors: string[]
  result: FleetImportCompleteEvent | undefined
  instancesFound?: number | undefined
  onClear: () => void
}) {
  const hasContent =
    progress.length > 0 ||
    errors.length > 0 ||
    !!result ||
    instancesFound !== undefined

  return (
    <AnimatePresence>
      {hasContent && (
        <m.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="overflow-hidden"
        >
          <div className="bg-surface-layout-2/50 rounded-lg border border-border-layout-1">
            <Scrollable className="max-h-64">
              <VStack className="gap-1 items-stretch p-4">
                {instancesFound !== undefined && (
                  <HStack className="gap-2 items-center">
                    <Icon
                      name="search"
                      label="Discovered"
                      className="w-3.5 h-3.5 text-content-layout-3"
                    />
                    <Text level="caption" className="text-content-layout-2">
                      Found {instancesFound} instance
                      {instancesFound === 1 ? '' : 's'}
                    </Text>
                  </HStack>
                )}
                {progress.map((entry, index) => (
                  <HStack key={index} className="gap-2 items-center">
                    <Tag
                      size="small"
                      variant={
                        entry.status === 'skipped' ? 'warning' : 'positive'
                      }
                      modifier="ghost"
                      label={entry.status}
                    />
                    <Text level="caption" className="text-content-layout-2">
                      {entry.message}
                    </Text>
                  </HStack>
                ))}
                {errors.map((message, index) => (
                  <HStack key={`err-${index}`} className="gap-2 items-start">
                    <Icon
                      name="alert"
                      label="Error"
                      className="w-3.5 h-3.5 text-content-negative-soft mt-0.5 shrink-0"
                    />
                    <Text
                      level="caption"
                      className="text-content-negative-soft"
                    >
                      {message}
                    </Text>
                  </HStack>
                ))}
                {result && (
                  <HStack className="gap-2 items-center pt-2">
                    <Icon
                      name={result.success ? 'tick-double' : 'alert'}
                      label="Result"
                      className={`w-4 h-4 ${result.success ? 'text-content-positive-soft' : 'text-content-negative-soft'}`}
                    />
                    <Text level="label-small" className="text-content-layout-1">
                      {result.imported} imported, {result.skipped} skipped,{' '}
                      {result.errors} errors
                    </Text>
                  </HStack>
                )}
                {(result || errors.length > 0) && (
                  <HStack className="justify-end pt-1">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Clear"
                      onClick={onClear}
                    />
                  </HStack>
                )}
              </VStack>
            </Scrollable>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  )
}

function AddTargetsTab({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-9 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
        data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
        data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
      data-active={active}
    >
      {label}
    </button>
  )
}

// Progressive disclosure inside the drawer: rarely-touched fields (Group,
// Password Env, Dry run) fold behind a toggle so the primary path (path/regions)
// leads (USE-025, VIS-103).
function AdvancedOptions({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-border-layout-1 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1.5 cursor-pointer"
      >
        <Icon
          name={open ? 'chevron-down' : 'chevron-right'}
          label=""
          className="w-4 h-4 text-content-layout-2"
        />
        <Text level="label-small" className="text-content-layout-2">
          Advanced options
        </Text>
      </button>
      <Show when={open}>
        <div className="pt-3">{children}</div>
      </Show>
    </div>
  )
}

function DryRunToggle({
  active,
  onToggle,
}: {
  active: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
        data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
        data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
      data-active={active}
    >
      Dry run
    </button>
  )
}

function FleetPage() {
  const queryClient = useQueryClient()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [addTab, setAddTab] = useState<'csv' | 'aws'>('csv')

  const { data: targets, isLoading } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
  })
  const members = targets?.members || []

  const { data: snapshotsData } = useQuery({
    queryKey: ['fleet-snapshots'],
    queryFn: fetchFleetSnapshots,
    staleTime: 30_000,
  })
  const latestSnapshot = snapshotsData?.snapshots?.[0]
  const snapshotCount = snapshotsData?.snapshots?.length ?? 0

  const { data: latestDetail } = useQuery({
    queryKey: ['fleet-snapshot-detail', latestSnapshot?.snapshot_id],
    queryFn: () => fetchFleetSnapshotDetail(latestSnapshot!.snapshot_id),
    enabled: !!latestSnapshot,
    staleTime: 60_000,
  })
  const verdictMap = useMemo(
    () => mapSnapshotVerdicts(latestDetail),
    [latestDetail]
  )

  const {
    check,
    state: statusState,
    results: statusResults,
    error: statusError,
  } = useFleetStatus()
  const isChecking = statusState === 'running'

  // Auto-run the connectivity check once per mount, as soon as targets load.
  const didAutoCheck = useRef(false)
  useEffect(() => {
    if (didAutoCheck.current || members.length === 0) return
    didAutoCheck.current = true
    check()
  }, [members.length, check])

  const {
    runAudit,
    state: auditState,
    targets: auditTargets,
    statusMessage: auditStatusMessage,
    summary: auditSummary,
    snapshotId: auditSnapshotId,
    error: auditError,
  } = useFleetAudit()
  const isAuditing = auditState === 'running'
  const auditTargetNames = Object.keys(auditTargets)
  const showAudit =
    isAuditing || auditState === 'complete' || auditState === 'error'

  // Scoreboard aggregation (VIS-011): pre-process the per-target connectivity +
  // sizing signal the table already shows into "is my fleet OK?" — no new data.
  // Mirrors StatusCell exactly: 'ok' → reachable; any settled non-'ok'/'checking'
  // status (backend emits 'failed') is the "Unreachable" row → needs attention.
  const scoreboard = useMemo(() => {
    let reachable = 0
    let needAttention = 0
    for (const member of members) {
      const connStatus = statusResults[member.name]?.status
      const verdict = verdictMap[member.name]
      if (connStatus === 'ok') reachable += 1
      const unreachable =
        !!connStatus && connStatus !== 'ok' && connStatus !== 'checking'
      const offTarget = verdict?.verdict
        ? PROBLEM_VERDICTS.has(verdict.verdict)
        : false
      if (unreachable || offTarget) needAttention += 1
    }
    return { total: members.length, reachable, needAttention }
  }, [members, statusResults, verdictMap])

  const handleAudit = async () => {
    await runAudit()
    queryClient.invalidateQueries({ queryKey: ['fleet-snapshots'] })
    queryClient.invalidateQueries({ queryKey: ['fleet-snapshot-detail'] })
  }

  // Import form
  const [csvPath, setCsvPath] = useState('')
  const [importGroup, setImportGroup] = useState('')
  const [passwordEnv, setPasswordEnv] = useState('FLEET_PASS')
  const [dryRun, setDryRun] = useState(false)
  const {
    runImport,
    state: importState,
    progress: importProgress,
    result: importResult,
    errors: importErrors,
    reset: resetImport,
  } = useFleetImport()
  const isImporting = importState === 'running'

  const handleImport = async () => {
    if (!csvPath.trim()) return
    await runImport({
      csv_file: csvPath.trim(),
      password_env: passwordEnv.trim() || 'FLEET_PASS',
      group: importGroup.trim() || undefined,
      dry_run: dryRun,
    })
    if (!dryRun) {
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] })
    }
  }

  // AWS discover form
  const [regionsInput, setRegionsInput] = useState('')
  const [engineFilter, setEngineFilter] = useState<
    'all' | 'postgresql' | 'mysql'
  >('all')
  const [namePattern, setNamePattern] = useState('')
  const [discoverGroup, setDiscoverGroup] = useState('')
  const [discoverPasswordEnv, setDiscoverPasswordEnv] = useState('FLEET_PASS')
  const [discoverDryRun, setDiscoverDryRun] = useState(false)
  const {
    runDiscover,
    state: discoverState,
    progress: discoverProgress,
    result: discoverResult,
    errors: discoverErrors,
    instancesFound: discoverInstancesFound,
    reset: resetDiscover,
  } = useFleetDiscover()
  const isDiscovering = discoverState === 'running'

  const parseRegions = (raw: string) =>
    raw
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean)

  const handleDiscover = async () => {
    const regions = parseRegions(regionsInput)
    if (regions.length === 0) return
    const completion = await runDiscover({
      regions,
      engine_filter: engineFilter,
      name_pattern: namePattern.trim() || undefined,
      group: discoverGroup.trim() || undefined,
      password_env: discoverPasswordEnv.trim() || 'FLEET_PASS',
      dry_run: discoverDryRun,
    })
    if (!discoverDryRun && completion && completion.imported > 0) {
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] })
    }
  }

  const populated = !isLoading && members.length > 0
  const empty = !isLoading && members.length === 0

  return (
    <div className="space-y-6 w-full">
      {/* Region A — Page head: name = heading, secondary action on the right */}
      <HStack className="justify-between items-center">
        <Text as="h1" level="headline-3" className="text-content-layout-1">
          Fleet
        </Text>
        <Show when={populated}>
          <Button
            variant="primary"
            modifier="ghost"
            label="Add targets"
            icon="add"
            iconPosition="left"
            onClick={() => setDrawerOpen(true)}
          />
        </Show>
      </HStack>

      {/* Status error */}
      <Show when={!!statusError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 text-content-negative-soft"
            />
            <Text level="body-small" className="text-content-negative-soft">
              {statusError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Loading */}
      <Show when={isLoading}>
        <div className="rounded-2xl bg-surface-raised shadow-elevation-1 p-12">
          <VStack className="gap-3 items-center">
            <Spinner size="base" />
            <Text level="body-small" className="text-content-layout-2">
              Loading fleet…
            </Text>
          </VStack>
        </div>
      </Show>

      {/* Region B — PRIMARY: health scoreboard (highest elevation + accent bar) */}
      <Show when={populated}>
        <div className="relative overflow-hidden rounded-2xl bg-surface-raised shadow-elevation-1">
          <div
            className="absolute left-0 top-0 bottom-0 w-1 bg-surface-rising-solid"
            aria-hidden="true"
          />
          <div className="pl-6 pr-5 py-5">
            <HStack className="justify-between items-center gap-6 flex-wrap">
              <HStack className="gap-10 items-center">
                <StatTile value={scoreboard.total} label="targets" />
                <StatTile value={scoreboard.reachable} label="reachable" />
                <StatTile
                  value={scoreboard.needAttention}
                  label="need attention"
                  accent
                />
              </HStack>
              <Button
                variant="primary"
                modifier="solid"
                label="Audit fleet"
                icon="document-validation"
                iconPosition="left"
                onClick={handleAudit}
                loading={isAuditing}
                disabled={isAuditing || members.length === 0}
              />
            </HStack>
          </div>
        </div>
      </Show>

      {/* Empty state — illustrated hero, table chrome hidden (VIS-102, VIS-103) */}
      <Show when={empty}>
        <div className="relative overflow-hidden rounded-2xl bg-surface-raised shadow-elevation-1">
          <div
            className="absolute left-0 top-0 bottom-0 w-1 bg-surface-rising-solid"
            aria-hidden="true"
          />
          <div className="px-8 py-14">
            <VStack className="gap-4 items-center text-center">
              <div className="w-16 h-16 rounded-2xl bg-surface-rising-soft flex items-center justify-center">
                <Icon
                  name="dashboard"
                  label="Fleet"
                  className="w-8 h-8 text-content-rising-soft"
                />
              </div>
              <VStack className="gap-1 items-center">
                <Text level="headline-4" className="text-content-layout-1">
                  No databases in your fleet yet.
                </Text>
                <Text
                  level="body-small"
                  className="text-content-layout-2 max-w-md"
                >
                  Add your databases to track reachability and right-sizing
                  across the whole fleet at a glance.
                </Text>
              </VStack>
              <Button
                variant="primary"
                modifier="solid"
                label="Add your first target"
                icon="add"
                iconPosition="left"
                onClick={() => setDrawerOpen(true)}
              />
            </VStack>
          </div>
        </div>
      </Show>

      {/* Region C — SECONDARY: signal-led targets table */}
      <Show when={populated}>
        <SectionCard
          icon="database"
          title={`Targets (${members.length})`}
          actions={
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Refresh"
              icon="connect"
              iconPosition="left"
              onClick={() => check()}
              loading={isChecking}
              disabled={isChecking || members.length === 0}
            />
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2/30">
                  {['Target', 'Connectivity', 'Sizing'].map((header) => (
                    <TableHeaderCell key={header}>{header}</TableHeaderCell>
                  ))}
                  <th className="px-4 py-3 w-10" aria-hidden="true" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-layout-1">
                {members.map((member) => (
                  <MemberRow
                    key={member.name}
                    member={member}
                    status={statusResults[member.name]}
                    verdict={verdictMap[member.name]}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      </Show>

      {/* Audit progress / results */}
      <Show when={showAudit}>
        <SectionCard
          icon="document-validation"
          title="Fleet Audit"
          actions={
            isAuditing && auditStatusMessage ? (
              <HStack className="gap-2 items-center">
                <Spinner size="base" />
                <Text level="caption" className="text-content-layout-3">
                  {auditStatusMessage}
                </Text>
              </HStack>
            ) : undefined
          }
        >
          <Show when={!!auditError}>
            <div className="px-5 py-3 bg-surface-negative-soft/30 border-b border-border-negative-soft">
              <HStack className="gap-2 items-center">
                <Icon
                  name="alert"
                  label="Error"
                  className="w-4 h-4 text-content-negative-soft"
                />
                <Text level="body-small" className="text-content-negative-soft">
                  {auditError}
                </Text>
              </HStack>
            </div>
          </Show>

          <Show when={auditTargetNames.length === 0 && isAuditing}>
            <div className="p-8">
              <VStack className="gap-3 items-center">
                <Spinner size="base" />
                <Text level="body-small" className="text-content-layout-3">
                  Starting audit...
                </Text>
              </VStack>
            </div>
          </Show>

          <Show when={auditTargetNames.length > 0}>
            <div className="divide-y divide-border-layout-1">
              {auditTargetNames.map((name) => (
                <AuditTargetRow
                  key={name}
                  name={name}
                  state={auditTargets[name]}
                />
              ))}
            </div>
          </Show>

          <Show when={auditState === 'complete' && !!auditSummary}>
            <div className="px-5 py-3 border-t border-border-layout-1 bg-surface-layout-2/50">
              <VStack className="gap-2 items-stretch">
                <HStack className="gap-2 items-center flex-wrap">
                  <Icon
                    name="tick-double"
                    label="Complete"
                    className="w-4 h-4 text-content-positive-soft"
                  />
                  <Text level="label-small" className="text-content-layout-1">
                    {auditSummary?.successes ?? 0} audited,{' '}
                    {auditSummary?.failures ?? 0} failed
                  </Text>
                  <Show when={!!auditSnapshotId}>
                    <Tag
                      size="small"
                      variant="informative"
                      modifier="ghost"
                      label={`snapshot ${auditSnapshotId}`}
                    />
                  </Show>
                </HStack>
                {auditSummary && <FleetInsights summary={auditSummary} />}
                {/* PQL hand-raiser (dma.4): a completed audit across more than
                    one instance is fleet intent. */}
                <Show when={scoreboard.total > 1}>
                  <HandRaiser
                    signal="fleet_audit"
                    tone="info"
                    message={`Audit complete across ${scoreboard.total} instances. Rolling out caching across a fleet is exactly what we help with.`}
                  />
                </Show>
              </VStack>
            </div>
          </Show>
        </SectionCard>
      </Show>

      {/* Region D — TERTIARY: History & drift (collapsed disclosure) */}
      <Show when={populated || snapshotCount > 0}>
        <HistoryDriftSection />
      </Show>

      {/* Add Targets drawer */}
      <Drawer open={drawerOpen} onOpenChange={setDrawerOpen} direction="right">
        <DrawerContentContainer>
          {drawerOpen && (
            <DrawerContent size="XLarge" direction="right" className="p-0">
              <DrawerHeader className="p-5 border-b border-border-layout-1">
                <DrawerTitle>Add Targets</DrawerTitle>
                <DrawerDescription>
                  Bulk-add database targets by importing a CSV or discovering
                  AWS RDS/Aurora instances.
                </DrawerDescription>
                <HStack className="gap-1 pt-3">
                  <AddTargetsTab
                    active={addTab === 'csv'}
                    label="Import CSV"
                    onClick={() => setAddTab('csv')}
                  />
                  <AddTargetsTab
                    active={addTab === 'aws'}
                    label="Discover AWS"
                    onClick={() => setAddTab('aws')}
                  />
                </HStack>
              </DrawerHeader>

              <Scrollable className="flex-1 p-5">
                <Show when={addTab === 'csv'}>
                  <VStack className="gap-4 items-stretch">
                    <Text level="body-small" className="text-content-layout-3">
                      Bulk-add targets from a CSV file with columns: name, host,
                      engine (plus optional port, database, user, group, tags).
                    </Text>
                    <PathPicker
                      value={csvPath}
                      onChange={setCsvPath}
                      fileExt="csv"
                      label="CSV Path"
                      disabled={isImporting}
                    />
                    <AdvancedOptions>
                      <VStack className="gap-3 items-stretch">
                        <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3 items-end">
                          <div>
                            <Text
                              level="caption"
                              className="text-content-layout-3 mb-1 block"
                            >
                              Group
                            </Text>
                            <BaseInputText
                              name="fleet-import-group"
                              value={importGroup}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) => setImportGroup(e.target.value)}
                              placeholder="optional"
                            />
                          </div>
                          <div>
                            <Text
                              level="caption"
                              className="text-content-layout-3 mb-1 block"
                            >
                              Password Env
                            </Text>
                            <BaseInputText
                              name="fleet-password-env"
                              value={passwordEnv}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) => setPasswordEnv(e.target.value)}
                              placeholder="FLEET_PASS"
                            />
                          </div>
                        </div>
                        <HStack className="justify-start">
                          <DryRunToggle
                            active={dryRun}
                            onToggle={() => setDryRun(!dryRun)}
                          />
                        </HStack>
                      </VStack>
                    </AdvancedOptions>

                    <HStack className="gap-3 items-center justify-end">
                      <Button
                        variant="primary"
                        modifier="solid"
                        label={dryRun ? 'Preview Import' : 'Import'}
                        icon="add"
                        iconPosition="left"
                        onClick={handleImport}
                        loading={isImporting}
                        disabled={!csvPath.trim() || isImporting}
                      />
                    </HStack>

                    <StreamLog
                      progress={importProgress}
                      errors={importErrors}
                      result={importResult}
                      onClear={resetImport}
                    />
                  </VStack>
                </Show>

                <Show when={addTab === 'aws'}>
                  <VStack className="gap-4 items-stretch">
                    <Text level="body-small" className="text-content-layout-3">
                      Discover RDS/Aurora instances via your local AWS
                      credentials (~/.aws, env, or SSO). No credentials are
                      entered here.
                    </Text>
                    <div>
                      <Text
                        level="caption"
                        className="text-content-layout-3 mb-1 block"
                      >
                        Regions (comma-separated)
                      </Text>
                      <BaseInputText
                        name="fleet-discover-regions"
                        value={regionsInput}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setRegionsInput(e.target.value)
                        }
                        placeholder="us-east-1, us-west-2"
                      />
                    </div>
                    <div>
                      <Text
                        level="caption"
                        className="text-content-layout-3 mb-1 block"
                      >
                        Engine
                      </Text>
                      <HStack className="gap-1">
                        {(['all', 'postgresql', 'mysql'] as const).map(
                          (engine) => (
                            <button
                              key={engine}
                              type="button"
                              onClick={() => setEngineFilter(engine)}
                              className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
                                data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
                                data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
                              data-active={engineFilter === engine}
                            >
                              {engine === 'all' ? 'All' : engineLabel(engine)}
                            </button>
                          )
                        )}
                      </HStack>
                    </div>
                    <div>
                      <Text
                        level="caption"
                        className="text-content-layout-3 mb-1 block"
                      >
                        Name Pattern
                      </Text>
                      <BaseInputText
                        name="fleet-discover-name-pattern"
                        value={namePattern}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setNamePattern(e.target.value)
                        }
                        placeholder="prod-* (optional glob)"
                      />
                    </div>
                    <AdvancedOptions>
                      <VStack className="gap-3 items-stretch">
                        <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3 items-end">
                          <div>
                            <Text
                              level="caption"
                              className="text-content-layout-3 mb-1 block"
                            >
                              Group
                            </Text>
                            <BaseInputText
                              name="fleet-discover-group"
                              value={discoverGroup}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) => setDiscoverGroup(e.target.value)}
                              placeholder="optional"
                            />
                          </div>
                          <div>
                            <Text
                              level="caption"
                              className="text-content-layout-3 mb-1 block"
                            >
                              Password Env
                            </Text>
                            <BaseInputText
                              name="fleet-discover-password-env"
                              value={discoverPasswordEnv}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) => setDiscoverPasswordEnv(e.target.value)}
                              placeholder="FLEET_PASS"
                            />
                          </div>
                        </div>
                        <HStack className="justify-start">
                          <DryRunToggle
                            active={discoverDryRun}
                            onToggle={() => setDiscoverDryRun(!discoverDryRun)}
                          />
                        </HStack>
                      </VStack>
                    </AdvancedOptions>

                    <HStack className="gap-3 items-center justify-end">
                      <Button
                        variant="primary"
                        modifier="solid"
                        label={discoverDryRun ? 'Preview Discover' : 'Discover'}
                        icon="search"
                        iconPosition="left"
                        onClick={handleDiscover}
                        loading={isDiscovering}
                        disabled={
                          parseRegions(regionsInput).length === 0 ||
                          isDiscovering
                        }
                      />
                    </HStack>

                    <StreamLog
                      progress={discoverProgress}
                      errors={discoverErrors}
                      result={discoverResult}
                      instancesFound={discoverInstancesFound}
                      onClear={resetDiscover}
                    />
                  </VStack>
                </Show>
              </Scrollable>
            </DrawerContent>
          )}
        </DrawerContentContainer>
      </Drawer>
    </div>
  )
}
