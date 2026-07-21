/**
 * Detail body for a saved audit/capture run — moved into this route-ignored
 * sibling (TanStack skips `-`-prefixed files) so the `-audit-views` → SQLDisplay
 * → CodeMirror import it pulls stays out of the eager route reference module.
 * `AuditRunDetailPage` is exported (and prop-driven) so it renders without router
 * context in tests; the route wrapper in `audit_.runs.$runId.tsx` feeds it loader
 * data + the path param. When this body lived in the route module and was
 * exported for the test, its CodeMirror import got modulepreloaded on every
 * route. [FIX-1 / Defect D-1; feedback-triage-2 §1.3, USE-041/042/097]
 */

import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { isWorkloadRun } from '../lib/useAudit'
import type { AuditReport, WorkloadRun } from '../types/audit'
import { AuditReportView, formatDate, WorkloadRunView } from './-audit-views'

/** Back link for the detail view. The route module's pending/error views keep
 * their own copy so this SQL-bearing sibling is imported by exactly one split
 * node (the route `component`) and stays fully code-split. [FIX-1 / Defect D-1] */
function BackToHealthCheck() {
  return (
    <Link
      to="/audit"
      className="inline-flex items-center gap-1.5 text-content-layout-3 hover:text-content-layout-1 transition-colors"
    >
      <Icon
        name="chevron-left"
        label=""
        aria-hidden="true"
        className="w-4 h-4"
      />
      <Text level="label-small">Back to Health Check</Text>
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
  data: AuditReport | WorkloadRun
  runId: string
}) {
  const workload = isWorkloadRun(data)
  const savedAt = workload
    ? (data as WorkloadRun).started_at
    : (data as AuditReport).audited_at
  const kindLabel = workload ? 'Workload capture' : 'Quick audit'

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
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              {kindLabel}
            </Text>
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
        </HStack>
      </m.div>

      {/* Body reuses the /audit report/workload views verbatim. */}
      {workload ? (
        <WorkloadRunView run={data as WorkloadRun} />
      ) : (
        <AuditReportView report={data as AuditReport} />
      )}
    </div>
  )
}
