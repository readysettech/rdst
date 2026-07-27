/**
 * Detail body for a saved audit/capture run — moved into this route-ignored
 * sibling (TanStack skips `-`-prefixed files) so the report's SQL rendering
 * stays out of the eager route reference module.
 * `AuditRunDetailPage` is exported (and prop-driven) so it renders without router
 * context in tests; the route wrapper in `audit_.runs.$runId.tsx` feeds it loader
 * data + the path param. When this body lived in the route module and was
 * exported for the test, its CodeMirror import got modulepreloaded on every
 * route. [FIX-1 / Defect D-1; feedback-triage-2 §1.3, USE-041/042/097]
 */

import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import {
  FullAuditReportView,
  WorkloadRunView,
} from '../components/audit/report/AuditReportView'
import { FleetSnapshotView } from '../components/audit/report/FleetSnapshotView'
import { EmailReportDialog } from '../components/EmailReportDialog'
import { formatDate } from '../lib/auditReportFormat'
import { isWorkloadRun } from '../lib/useAudit'
import type { FleetSnapshotDetail } from '../lib/useFleet'
import type { AuditReport, WorkloadRun } from '../types/audit'

export type AuditRunDetailData =
  | { kind: 'audit'; data: AuditReport | WorkloadRun }
  | { kind: 'fleet'; data: FleetSnapshotDetail }

/** Back link for the detail view. The route module's pending/error views keep
 * their own copy so this SQL-bearing sibling is imported by exactly one split
 * node (the route `component`) and stays fully code-split. [FIX-1 / Defect D-1] */
/** The report is emailed straight from the saved run, so a capture with no
 * health analysis has nothing to send. */
function EmailReportButton({
  runId,
  kind,
  emailable,
}: {
  runId: string
  kind: 'audit' | 'fleet'
  emailable: boolean
}) {
  const [isOpen, setIsOpen] = useState(false)
  const button = (
    <Button
      variant="primary"
      modifier="ghost"
      icon="arrow-up-right"
      iconPosition="right"
      label="Email me this report"
      disabled={!emailable}
      onClick={() => setIsOpen(true)}
    />
  )
  return (
    <>
      {emailable ? (
        button
      ) : (
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div>{button}</div>
            </TooltipTrigger>
            <TooltipContent label="This capture has no health analysis, so there is no report to email. Run a health check on the target." />
          </Tooltip>
        </TooltipProvider>
      )}
      {/* Mounted only while open so the detail page itself stays free of the
          dialog's identity query. */}
      {isOpen && (
        <EmailReportDialog
          isOpen
          onClose={() => setIsOpen(false)}
          runId={runId}
          kind={kind}
        />
      )}
    </>
  )
}

function BackToHealthCheck() {
  return (
    <Link
      to="/audit"
      search={{ tab: 'history' }}
      hash="history"
      className="inline-flex items-center gap-1.5 text-content-layout-3 hover:text-content-layout-1 transition-colors"
    >
      <Icon
        name="chevron-left"
        label=""
        aria-hidden="true"
        className="w-4 h-4"
      />
      <Text level="label-small">Back to Reports</Text>
    </Link>
  )
}

/**
 * The detail body. Exported (and prop-driven) so it renders without router
 * context in tests; the route wrapper feeds it loader data + the path param.
 */
export function AuditRunDetailPage({
  data,
  runId,
}: {
  data: AuditRunDetailData
  runId: string
}) {
  const payload = data.data
  // The audit-runs endpoint also resolves fleet snapshot ids (returning the
  // fleet shape), so the loader can mislabel a fleet run as 'audit'. Trust the
  // payload shape: a fleet snapshot has snapshot_id + targets_audited, which a
  // single-target AuditReport never does. Without this, fleet runs render
  // through the single-target view and come out blank.
  const fleet =
    data.kind === 'fleet' ||
    (!!payload &&
      typeof payload === 'object' &&
      'snapshot_id' in payload &&
      'targets_audited' in payload)
  const workload = !fleet && isWorkloadRun(payload)
  const savedAt = fleet
    ? (payload as FleetSnapshotDetail).created_at
    : workload
      ? (payload as WorkloadRun).started_at
      : (payload as AuditReport).audited_at
  const kindLabel = fleet
    ? 'Fleet health report'
    : workload
      ? 'Workload capture report'
      : 'Health check report'
  const identityLabel = fleet
    ? (payload as FleetSnapshotDetail).name || 'Fleet'
    : (payload as AuditReport | WorkloadRun).target_name || 'Database target'
  const fleetInsights = fleet
    ? (payload as FleetSnapshotDetail).fleet_insights
    : undefined
  const fleetHealthScore =
    typeof fleetInsights?.health_score === 'number'
      ? fleetInsights.health_score
      : undefined
  const fleetHealthLabel =
    typeof fleetInsights?.health_label === 'string'
      ? fleetInsights.health_label
      : undefined
  const emailable =
    fleet || !workload || !!(payload as WorkloadRun).health_analysis

  return (
    <div className="space-y-6 w-full">
      <BackToHealthCheck />

      {/* Header names the page after the run the user clicked (USE-041/042). */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center shrink-0">
            <Icon
              name="document-validation"
              label="Saved run"
              className="w-6 h-6 text-content-info-soft"
            />
          </div>
          <VStack className="gap-1 items-start min-w-0">
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {kindLabel}
            </Text>
            <Text
              as="h1"
              level="headline-2"
              className="text-content-layout-1 break-words"
            >
              {identityLabel}
              {fleet
                ? ` · ${(payload as FleetSnapshotDetail).targets_audited} targets`
                : ''}
            </Text>
            {fleetHealthScore != null && (
              <Tag
                variant={
                  fleetHealthScore >= 75
                    ? 'positive'
                    : fleetHealthScore >= 60
                      ? 'warning'
                      : 'negative'
                }
                modifier="ghost"
                label={`${fleetHealthLabel?.toUpperCase() || 'FLEET HEALTH'} · ${Math.round(fleetHealthScore)}/100`}
              />
            )}
            <HStack className="gap-2 items-baseline flex-wrap min-w-0">
              <Text
                level="mono-small"
                className="text-content-layout-3 truncate"
                data-testid="run-detail-id"
              >
                {runId}
              </Text>
              <Text level="caption" className="text-content-layout-3 shrink-0">
                Saved · {formatDate(savedAt)}
              </Text>
            </HStack>
          </VStack>
          <div className="ml-auto shrink-0">
            <EmailReportButton
              runId={runId}
              kind={fleet ? 'fleet' : 'audit'}
              emailable={emailable}
            />
          </div>
        </HStack>
      </m.div>

      {/* Body reuses the canonical full report/workload views. */}
      {fleet ? (
        <FleetSnapshotView
          detail={payload as FleetSnapshotDetail}
          aiCredentialInvalid={false}
        />
      ) : workload ? (
        <WorkloadRunView run={payload as WorkloadRun} />
      ) : (
        <FullAuditReportView
          report={payload as AuditReport}
          aiCredentialInvalid={false}
        />
      )}
    </div>
  )
}
