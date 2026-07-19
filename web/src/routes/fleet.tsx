import { useEffect, useMemo, useRef, useState } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { Button } from '@rs/ui-new/button';
import { Card } from '@rs/ui-new/card';
import { Icon } from '@rs/ui-new/icon';
import type { IconStrokeName } from '@rs/ui-icons/icon-name';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { Tag } from '@rs/ui-new/tag';
import { Text } from '@rs/ui-new/text';
import { HStack, VStack } from '@rs/ui-new/stack';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import {
  Drawer,
  DrawerContent,
  DrawerContentContainer,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@rs/ui-new/drawer';
import { Scrollable } from '@rs/ui-new/scrollable';
import { toast } from '@rs/ui-new/use-toast';
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
} from '../lib/useFleet';
import type {
  FleetAuditSummary,
  FleetAuditTargetState,
  FleetTargetVerdict,
} from '../lib/useFleet';
import type {
  FleetConnectivityEvent,
  FleetDiffResponse,
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
  FleetMember,
  FleetSnapshotSummary,
} from '../types/fleet';
import { formatTimestamp } from '../lib/formatters';
import { PathPicker } from '../components/PathPicker';

export const Route = createFileRoute('/fleet')({
  component: FleetPage,
});

function SectionCard({
  icon,
  title,
  actions,
  children,
}: {
  icon: IconStrokeName;
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="justify-between items-center">
            <HStack className="gap-2 items-center">
              <Icon name={icon} label={title} className="w-4 h-4 text-content-layout-3" />
              <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                {title}
              </Text>
            </HStack>
            {actions}
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  );
}

const VERDICT_LABELS: Record<
  string,
  { label: string; variant: 'positive' | 'warning' | 'negative' | 'informative' }
> = {
  right_sized: { label: 'Right-sized', variant: 'positive' },
  oversized: { label: 'Oversized', variant: 'warning' },
  under_provisioned: { label: 'Under-provisioned', variant: 'negative' },
  unknown: { label: 'Unknown', variant: 'informative' },
};

// Proper engine display names — the backend emits lowercase enum values, which
// CSS `capitalize` renders as "Postgresql"/"Mysql". [QW19]
const ENGINE_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
};
const engineLabel = (engine: string | null | undefined) =>
  engine ? (ENGINE_LABELS[engine.toLowerCase()] ?? engine) : '-';

function StatusCell({ result }: { result: FleetConnectivityEvent | undefined }) {
  if (!result) {
    return <Text level="caption" className="text-content-layout-3">-</Text>;
  }
  if (result.status === 'checking') {
    return <Spinner size="base" />;
  }
  if (result.status === 'ok') {
    return (
      <VStack className="gap-0.5 items-start">
        <Tag size="small" variant="positive" modifier="ghost" label={`${result.latency_ms}ms`} />
        {result.server_version && (
          <Text level="caption" className="text-content-layout-3 truncate max-w-48">
            {result.server_version}
          </Text>
        )}
      </VStack>
    );
  }
  return (
    <VStack className="gap-0.5 items-start">
      <Tag size="small" variant="negative" modifier="ghost" label="Unreachable" />
      {result.error && (
        <Text level="caption" className="text-content-layout-3 truncate max-w-48">
          {result.error}
        </Text>
      )}
    </VStack>
  );
}

function LastAuditCell({ verdict }: { verdict: FleetTargetVerdict | undefined }) {
  if (!verdict?.verdict) {
    return <Text level="caption" className="text-content-layout-3">-</Text>;
  }
  const info = VERDICT_LABELS[verdict.verdict] || VERDICT_LABELS.unknown;
  return (
    <VStack className="gap-0.5 items-start">
      <Tag size="small" variant={info.variant} modifier="ghost" label={info.label} />
      <Text level="caption" className="text-content-layout-3">
        cache {verdict.cacheScore ?? '-'}/100
      </Text>
    </VStack>
  );
}

function MemberRow({
  member,
  status,
  verdict,
}: {
  member: FleetMember;
  status: FleetConnectivityEvent | undefined;
  verdict: FleetTargetVerdict | undefined;
}) {
  const hasBadges = !!member.group || (member.tags?.length ?? 0) > 0;
  return (
    <tr className="hover:bg-surface-layout-2/50 transition-colors">
      <td className="px-4 py-3">
        <VStack className="gap-1 items-start">
          <Text level="label-small" className="text-content-layout-1">
            {member.name}
          </Text>
          {member.instance_class && (
            <Text level="caption" className="text-content-layout-3">
              {member.instance_class}
            </Text>
          )}
          {hasBadges && (
            <HStack className="gap-1 flex-wrap">
              {member.group && (
                <Tag size="small" variant="primary" modifier="ghost" label={member.group} />
              )}
              {(member.tags || []).map((tag) => (
                <Tag key={tag} size="small" variant="informative" modifier="ghost" label={tag} />
              ))}
            </HStack>
          )}
        </VStack>
      </td>
      <td className="px-4 py-3">
        <Tag size="small" variant="informative" modifier="ghost" label={engineLabel(member.engine)} />
      </td>
      <td className="px-4 py-3">
        <Text level="mono-small" className="text-content-layout-2 break-all">
          {member.host}:{member.port}
        </Text>
      </td>
      <td className="px-4 py-3">
        <Text level="mono-small" className="text-content-layout-2">
          {member.database || '-'}
        </Text>
      </td>
      <td className="px-4 py-3">
        <LastAuditCell verdict={verdict} />
      </td>
      <td className="px-4 py-3">
        <StatusCell result={status} />
      </td>
    </tr>
  );
}

function DiffValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-content-layout-3">-</span>;
  }
  if (typeof value === 'object') {
    return <span className="text-content-layout-2">{JSON.stringify(value)}</span>;
  }
  return <span className="text-content-layout-2">{String(value)}</span>;
}

function SnapshotDiff({ diff }: { diff: FleetDiffResponse }) {
  const hasChanges =
    diff.entries.length > 0 ||
    diff.new_targets.length > 0 ||
    diff.removed_targets.length > 0;

  return (
    <div className="p-5 border-b border-border-layout-1 bg-surface-layout-2/30">
      <VStack className="gap-3 items-stretch">
        <HStack className="gap-2 items-center flex-wrap">
          <Text level="label-small" className="text-content-layout-1">
            Diff
          </Text>
          <Tag size="small" variant="informative" modifier="ghost" label={`baseline ${formatTimestamp(diff.baseline_date)}`} />
          <Text level="caption" className="text-content-layout-3">
            {'->'}
          </Text>
          <Tag size="small" variant="informative" modifier="ghost" label={`current ${formatTimestamp(diff.current_date)}`} />
        </HStack>

        <Show when={diff.new_targets.length > 0 || diff.removed_targets.length > 0}>
          <HStack className="gap-2 items-center flex-wrap">
            {diff.new_targets.map((name) => (
              <Tag key={`new-${name}`} size="small" variant="positive" modifier="ghost" label={`+ ${name}`} />
            ))}
            {diff.removed_targets.map((name) => (
              <Tag key={`removed-${name}`} size="small" variant="negative" modifier="ghost" label={`- ${name}`} />
            ))}
          </HStack>
        </Show>

        <Show when={diff.entries.length > 0}>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2/30">
                  {['Target', 'Field', 'Old', 'New', 'Change %'].map((header) => (
                    <th
                      key={header}
                      className="px-4 py-2 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium"
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-layout-1">
                {diff.entries.map((entry, index) => (
                  <tr key={`${entry.target_name}-${entry.field_name}-${index}`}>
                    <td className="px-4 py-2">
                      <Text level="label-small" className="text-content-layout-1">
                        {entry.target_name}
                      </Text>
                    </td>
                    <td className="px-4 py-2">
                      <Text level="mono-small" className="text-content-layout-2">
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
                        <Text level="mono-small" className="text-content-layout-3">
                          {'->'}
                        </Text>
                        <Text level="mono-small">
                          <DiffValue value={entry.new_value} />
                        </Text>
                      </HStack>
                    </td>
                    <td className="px-4 py-2">
                      {entry.change_pct === null || entry.change_pct === undefined ? (
                        <Text level="mono-small" className="text-content-layout-3">-</Text>
                      ) : (
                        <Tag
                          size="small"
                          variant={entry.change_pct >= 0 ? 'positive' : 'negative'}
                          modifier="ghost"
                          label={`${entry.change_pct >= 0 ? '+' : ''}${entry.change_pct.toFixed(1)}%`}
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
              <Icon name="tick-double" label="No changes" className="w-6 h-6 text-content-layout-3" />
              <Text level="body-small" className="text-content-layout-3">
                No differences between these snapshots.
              </Text>
            </VStack>
          </div>
        </Show>
      </VStack>
    </div>
  );
}

function SnapshotsSection() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['fleet-snapshots'],
    queryFn: fetchFleetSnapshots,
    staleTime: 30_000,
  });
  const snapshots = data?.snapshots || [];

  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [baselineId, setBaselineId] = useState<string>('');
  const [currentId, setCurrentId] = useState<string>('');
  const [diff, setDiff] = useState<FleetDiffResponse | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (snapshotId: string) => deleteFleetSnapshot(snapshotId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fleet-snapshots'] });
      toast({ title: 'Snapshot deleted', variant: 'positive' });
    },
    onError: (err: Error) => {
      toast({ title: 'Delete failed', description: err.message, variant: 'negative' });
    },
  });

  const diffMutation = useMutation({
    mutationFn: ({ baseline, current }: { baseline: string; current: string }) =>
      fetchFleetDiff(baseline, current),
    onSuccess: (result) => setDiff(result),
    onError: (err: Error) => {
      toast({ title: 'Diff failed', description: err.message, variant: 'negative' });
    },
  });

  const options = snapshots.map((snapshot) => ({
    value: snapshot.snapshot_id,
    label: `${formatTimestamp(snapshot.created_at)} — ${snapshot.targets_audited} targets`,
  }));
  const canCompare = !!baselineId && !!currentId && baselineId !== currentId;
  const showCompareRow = snapshots.length >= 2;

  const handleDelete = (snapshotId: string) => {
    deleteMutation.mutate(snapshotId, { onSettled: () => setConfirmingId(null) });
    if (snapshotId === baselineId) setBaselineId('');
    if (snapshotId === currentId) setCurrentId('');
    setDiff(null);
  };

  return (
    <SectionCard icon="package" title={`Snapshots (${snapshots.length})`}>
      <Show when={showCompareRow}>
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center flex-wrap">
            <Text level="label-small" className="text-content-layout-2 shrink-0">
              Compare:
            </Text>
            <div className="w-56">
              <BaseInputSelect
                name="snapshot-baseline"
                placeholder="Baseline"
                value={baselineId}
                onValueChange={(value) => {
                  setBaselineId(value);
                  setDiff(null);
                }}
                options={options}
              />
            </div>
            <Text level="caption" className="text-content-layout-3 shrink-0">
              vs
            </Text>
            <div className="w-56">
              <BaseInputSelect
                name="snapshot-current"
                placeholder="Current"
                value={currentId}
                onValueChange={(value) => {
                  setCurrentId(value);
                  setDiff(null);
                }}
                options={options}
              />
            </div>
            <Button
              variant="primary"
              modifier="solid"
              size="small"
              label="Compare"
              icon="connect"
              iconPosition="left"
              disabled={!canCompare || diffMutation.isPending}
              loading={diffMutation.isPending}
              onClick={() => {
                if (canCompare) diffMutation.mutate({ baseline: baselineId, current: currentId });
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
              <Icon name="package" label="No snapshots" className="w-7 h-7 text-content-layout-3" />
            </div>
            <Text level="body-small" className="text-content-layout-3 text-center max-w-md">
              No snapshots yet. Run a fleet audit to capture one.
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
                    <Icon name="alert" label="Warning" className="w-4 h-4 text-content-negative-soft" />
                    <Text level="body-small" className="text-content-layout-1">
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
                      <Text level="label-small" className="text-content-layout-1">
                        Fleet audit — {formatTimestamp(snapshot.created_at)}
                      </Text>
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={`${snapshot.targets_audited} targets`}
                      />
                    </HStack>
                    <Text level="mono-small" className="text-content-layout-3 truncate">
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
    </SectionCard>
  );
}

function AuditTargetRow({ name, state }: { name: string; state: FleetAuditTargetState }) {
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
                const verdict = VERDICT_LABELS[state.verdict || 'unknown'] || VERDICT_LABELS.unknown;
                return <Tag size="small" variant={verdict.variant} modifier="ghost" label={verdict.label} />;
              })()}
              <Text level="caption" className="text-content-layout-3">
                cache {state.cacheScore ?? '-'}/100
              </Text>
            </HStack>
          </Show>
          <Show when={state.status === 'error'}>
            <VStack className="gap-0.5 items-end">
              <Tag size="small" variant="negative" modifier="ghost" label="Failed" />
              {state.error && (
                <Text level="caption" className="text-content-layout-3 truncate max-w-64 text-right">
                  {state.error}
                </Text>
              )}
            </VStack>
          </Show>
        </div>
      </HStack>
    </div>
  );
}

function FleetInsights({ summary }: { summary: FleetAuditSummary }) {
  const insights = summary.fleet_insights;
  const healthScore =
    insights && typeof insights === 'object' && typeof insights.health_score === 'number'
      ? (insights.health_score as number)
      : undefined;
  const findingsRaw =
    insights && typeof insights === 'object'
      ? (insights.top_findings ?? insights.fleet_findings)
      : undefined;
  const findings = Array.isArray(findingsRaw) ? findingsRaw : [];
  const hasContent = healthScore !== undefined || findings.length > 0;

  if (!hasContent) {
    return (
      <Text level="caption" className="text-content-layout-3">
        Fleet insights unavailable
      </Text>
    );
  }

  const findingTitle = (f: unknown): string | undefined => {
    if (f && typeof f === 'object') {
      const rec = f as Record<string, unknown>;
      const title = rec.title ?? rec.finding ?? rec.summary;
      if (typeof title === 'string') return title;
    }
    if (typeof f === 'string') return f;
    return undefined;
  };

  return (
    <VStack className="gap-1.5 items-start">
      {healthScore !== undefined && (
        <HStack className="gap-2 items-center">
          <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
            Fleet health
          </Text>
          <Tag size="small" variant="informative" modifier="ghost" label={`${healthScore}/100`} />
        </HStack>
      )}
      {findings.slice(0, 4).map((f, index) => {
        const title = findingTitle(f);
        if (!title) return null;
        return (
          <HStack key={index} className="gap-2 items-start">
            <span className="mt-1.5 h-1 w-1 rounded-full bg-content-layout-3 shrink-0" />
            <Text level="caption" className="text-content-layout-2">
              {title}
            </Text>
          </HStack>
        );
      })}
    </VStack>
  );
}

function StreamLog({
  progress,
  errors,
  result,
  instancesFound,
  onClear,
}: {
  progress: FleetImportProgressEvent[];
  errors: string[];
  result: FleetImportCompleteEvent | undefined;
  instancesFound?: number | undefined;
  onClear: () => void;
}) {
  const hasContent =
    progress.length > 0 || errors.length > 0 || !!result || instancesFound !== undefined;

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
                    <Icon name="search" label="Discovered" className="w-3.5 h-3.5 text-content-layout-3" />
                    <Text level="caption" className="text-content-layout-2">
                      Found {instancesFound} instance{instancesFound === 1 ? '' : 's'}
                    </Text>
                  </HStack>
                )}
                {progress.map((entry, index) => (
                  <HStack key={index} className="gap-2 items-center">
                    <Tag
                      size="small"
                      variant={entry.status === 'skipped' ? 'warning' : 'positive'}
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
                    <Icon name="alert" label="Error" className="w-3.5 h-3.5 text-content-negative-soft mt-0.5 shrink-0" />
                    <Text level="caption" className="text-content-negative-soft">
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
                      {result.imported} imported, {result.skipped} skipped, {result.errors} errors
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
  );
}

function AddTargetsTab({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
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
  );
}

function FleetPage() {
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [addTab, setAddTab] = useState<'csv' | 'aws'>('csv');

  const { data: targets, isLoading } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
  });
  const members = targets?.members || [];

  const { data: snapshotsData } = useQuery({
    queryKey: ['fleet-snapshots'],
    queryFn: fetchFleetSnapshots,
    staleTime: 30_000,
  });
  const latestSnapshot = snapshotsData?.snapshots?.[0];

  const { data: latestDetail } = useQuery({
    queryKey: ['fleet-snapshot-detail', latestSnapshot?.snapshot_id],
    queryFn: () => fetchFleetSnapshotDetail(latestSnapshot!.snapshot_id),
    enabled: !!latestSnapshot,
    staleTime: 60_000,
  });
  const verdictMap = useMemo(() => mapSnapshotVerdicts(latestDetail), [latestDetail]);

  const {
    check,
    state: statusState,
    results: statusResults,
    error: statusError,
  } = useFleetStatus();
  const isChecking = statusState === 'running';

  // Auto-run the connectivity check once per mount, as soon as targets load.
  const didAutoCheck = useRef(false);
  useEffect(() => {
    if (didAutoCheck.current || members.length === 0) return;
    didAutoCheck.current = true;
    check();
  }, [members.length, check]);

  const {
    runAudit,
    state: auditState,
    targets: auditTargets,
    statusMessage: auditStatusMessage,
    summary: auditSummary,
    snapshotId: auditSnapshotId,
    error: auditError,
  } = useFleetAudit();
  const isAuditing = auditState === 'running';
  const auditTargetNames = Object.keys(auditTargets);
  const showAudit = isAuditing || auditState === 'complete' || auditState === 'error';

  const handleAudit = async () => {
    await runAudit();
    queryClient.invalidateQueries({ queryKey: ['fleet-snapshots'] });
    queryClient.invalidateQueries({ queryKey: ['fleet-snapshot-detail'] });
  };

  // Import form
  const [csvPath, setCsvPath] = useState('');
  const [importGroup, setImportGroup] = useState('');
  const [passwordEnv, setPasswordEnv] = useState('FLEET_PASS');
  const [dryRun, setDryRun] = useState(false);
  const {
    runImport,
    state: importState,
    progress: importProgress,
    result: importResult,
    errors: importErrors,
    reset: resetImport,
  } = useFleetImport();
  const isImporting = importState === 'running';

  const handleImport = async () => {
    if (!csvPath.trim()) return;
    await runImport({
      csv_file: csvPath.trim(),
      password_env: passwordEnv.trim() || 'FLEET_PASS',
      group: importGroup.trim() || undefined,
      dry_run: dryRun,
    });
    if (!dryRun) {
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] });
    }
  };

  // AWS discover form
  const [regionsInput, setRegionsInput] = useState('');
  const [engineFilter, setEngineFilter] = useState<'all' | 'postgresql' | 'mysql'>('all');
  const [namePattern, setNamePattern] = useState('');
  const [discoverGroup, setDiscoverGroup] = useState('');
  const [discoverPasswordEnv, setDiscoverPasswordEnv] = useState('FLEET_PASS');
  const [discoverDryRun, setDiscoverDryRun] = useState(false);
  const {
    runDiscover,
    state: discoverState,
    progress: discoverProgress,
    result: discoverResult,
    errors: discoverErrors,
    instancesFound: discoverInstancesFound,
    reset: resetDiscover,
  } = useFleetDiscover();
  const isDiscovering = discoverState === 'running';

  const parseRegions = (raw: string) =>
    raw
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean);

  const handleDiscover = async () => {
    const regions = parseRegions(regionsInput);
    if (regions.length === 0) return;
    const completion = await runDiscover({
      regions,
      engine_filter: engineFilter,
      name_pattern: namePattern.trim() || undefined,
      group: discoverGroup.trim() || undefined,
      password_env: discoverPasswordEnv.trim() || 'FLEET_PASS',
      dry_run: discoverDryRun,
    });
    if (!discoverDryRun && completion && completion.imported > 0) {
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] });
    }
  };

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon name="dashboard" label="Fleet" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Fleet
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Health and sizing at a glance across all configured database targets.
              </Text>
            </VStack>
          </HStack>
          <HStack className="gap-2 items-center">
            <Button
              variant="primary"
              modifier="ghost"
              label="Add Targets"
              icon="add"
              iconPosition="left"
              onClick={() => setDrawerOpen(true)}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Audit Fleet"
              icon="document-validation"
              iconPosition="left"
              onClick={handleAudit}
              loading={isAuditing}
              disabled={isAuditing || members.length === 0}
            />
          </HStack>
        </HStack>
      </m.div>

      {/* Status error */}
      <Show when={!!statusError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
            <Text level="body-small" className="text-content-negative-soft">
              {statusError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Targets (hero) */}
      <SectionCard
        icon="database"
        title={`Targets (${members.length})`}
        actions={
          <HStack className="gap-3 items-center">
            <Show when={!!latestSnapshot}>
              <Text level="caption" className="text-content-layout-3">
                last audit {latestSnapshot ? formatTimestamp(latestSnapshot.created_at) : ''}
              </Text>
            </Show>
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
          </HStack>
        }
      >
        <Show when={isLoading}>
          <div className="p-12">
            <VStack className="gap-3 items-center">
              <Spinner size="base" />
              <Text level="body-small" className="text-content-layout-3">
                Loading fleet targets...
              </Text>
            </VStack>
          </div>
        </Show>

        <Show when={!isLoading && members.length === 0}>
          <div className="p-12">
            <VStack className="gap-3 items-center">
              <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                <Icon name="dashboard" label="No targets" className="w-7 h-7 text-content-layout-3" />
              </div>
              <Text level="body-small" className="text-content-layout-3">
                No fleet targets configured. Use Add Targets to import a CSV or discover from AWS.
              </Text>
            </VStack>
          </div>
        </Show>

        <Show when={!isLoading && members.length > 0}>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2/30">
                  {['Name', 'Engine', 'Host', 'Database', 'Last Audit', 'Status'].map((header) => (
                    <th
                      key={header}
                      className="px-4 py-3 text-left text-xs text-content-layout-3 uppercase tracking-wider font-medium"
                    >
                      {header}
                    </th>
                  ))}
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
        </Show>
      </SectionCard>

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
                <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
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
                <AuditTargetRow key={name} name={name} state={auditTargets[name]} />
              ))}
            </div>
          </Show>

          <Show when={auditState === 'complete' && !!auditSummary}>
            <div className="px-5 py-3 border-t border-border-layout-1 bg-surface-layout-2/50">
              <VStack className="gap-2 items-stretch">
                <HStack className="gap-2 items-center flex-wrap">
                  <Icon name="tick-double" label="Complete" className="w-4 h-4 text-content-positive-soft" />
                  <Text level="label-small" className="text-content-layout-1">
                    {auditSummary?.successes ?? 0} audited, {auditSummary?.failures ?? 0} failed
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
              </VStack>
            </div>
          </Show>
        </SectionCard>
      </Show>

      {/* Snapshots */}
      <SnapshotsSection />

      {/* Add Targets drawer */}
      <Drawer open={drawerOpen} onOpenChange={setDrawerOpen} direction="right">
        <DrawerContentContainer>
          {drawerOpen && (
            <DrawerContent size="XLarge" direction="right" className="p-0">
              <DrawerHeader className="p-5 border-b border-border-layout-1">
                <DrawerTitle>Add Targets</DrawerTitle>
                <DrawerDescription>
                  Bulk-add database targets by importing a CSV or discovering AWS RDS/Aurora
                  instances.
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
                      Bulk-add targets from a CSV file with columns: name, host, engine (plus
                      optional port, database, user, group, tags).
                    </Text>
                    <div className="grid grid-cols-1 gap-3 items-end">
                      <PathPicker
                        value={csvPath}
                        onChange={setCsvPath}
                        fileExt="csv"
                        label="CSV Path"
                        disabled={isImporting}
                      />
                      <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3 items-end">
                        <div>
                          <Text level="caption" className="text-content-layout-3 mb-1 block">Group</Text>
                          <BaseInputText
                            name="fleet-import-group"
                            value={importGroup}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setImportGroup(e.target.value)}
                            placeholder="optional"
                          />
                        </div>
                        <div>
                          <Text level="caption" className="text-content-layout-3 mb-1 block">Password Env</Text>
                          <BaseInputText
                            name="fleet-password-env"
                            value={passwordEnv}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPasswordEnv(e.target.value)}
                            placeholder="FLEET_PASS"
                          />
                        </div>
                      </div>
                      <HStack className="gap-3 items-center justify-end">
                        <button
                          type="button"
                          onClick={() => setDryRun(!dryRun)}
                          className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
                            data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
                            data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
                          data-active={dryRun}
                        >
                          Dry run
                        </button>
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
                    </div>

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
                      Discover RDS/Aurora instances via your local AWS credentials (~/.aws, env,
                      or SSO). No credentials are entered here.
                    </Text>
                    <div className="grid grid-cols-1 gap-3 items-end">
                      <div>
                        <Text level="caption" className="text-content-layout-3 mb-1 block">
                          Regions (comma-separated)
                        </Text>
                        <BaseInputText
                          name="fleet-discover-regions"
                          value={regionsInput}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setRegionsInput(e.target.value)}
                          placeholder="us-east-1, us-west-2"
                        />
                      </div>
                      <div>
                        <Text level="caption" className="text-content-layout-3 mb-1 block">Engine</Text>
                        <HStack className="gap-1">
                          {(['all', 'postgresql', 'mysql'] as const).map((engine) => (
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
                          ))}
                        </HStack>
                      </div>
                      <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3 items-end">
                        <div>
                          <Text level="caption" className="text-content-layout-3 mb-1 block">Name Pattern</Text>
                          <BaseInputText
                            name="fleet-discover-name-pattern"
                            value={namePattern}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNamePattern(e.target.value)}
                            placeholder="prod-* (optional glob)"
                          />
                        </div>
                        <div>
                          <Text level="caption" className="text-content-layout-3 mb-1 block">Group</Text>
                          <BaseInputText
                            name="fleet-discover-group"
                            value={discoverGroup}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDiscoverGroup(e.target.value)}
                            placeholder="optional"
                          />
                        </div>
                        <div>
                          <Text level="caption" className="text-content-layout-3 mb-1 block">Password Env</Text>
                          <BaseInputText
                            name="fleet-discover-password-env"
                            value={discoverPasswordEnv}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDiscoverPasswordEnv(e.target.value)}
                            placeholder="FLEET_PASS"
                          />
                        </div>
                      </div>
                      <HStack className="gap-3 items-center justify-end">
                        <button
                          type="button"
                          onClick={() => setDiscoverDryRun(!discoverDryRun)}
                          className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
                            data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
                            data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
                          data-active={discoverDryRun}
                        >
                          Dry run
                        </button>
                        <Button
                          variant="primary"
                          modifier="solid"
                          label={discoverDryRun ? 'Preview Discover' : 'Discover'}
                          icon="search"
                          iconPosition="left"
                          onClick={handleDiscover}
                          loading={isDiscovering}
                          disabled={parseRegions(regionsInput).length === 0 || isDiscovering}
                        />
                      </HStack>
                    </div>

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
  );
}
