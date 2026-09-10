/**
 * Standalone detail route for a saved audit/capture run.
 *
 * Past runs used to open inline inside /audit (component-local `loadedReport`
 * state, URL frozen at /audit — not deep-linkable, back did nothing). This route
 * gives each saved run its own URL: the Reports list links here, the loader
 * fetches the saved run, and browser back returns to the launcher.
 *
 * The report / workload body lives in the route-ignored `-audit-run-detail-page`
 * sibling so the CodeMirror SQL stack stays code-split — an exported page in this eager route reference
 * module would pin CodeMirror into the eager graph. [FIX-1 / Defect D-1;
 * feedback-triage-2 §1.3, USE-041/042/097]
 */

import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { fetchRunDetail } from '../lib/useAudit'
import { fetchFleetSnapshotDetail } from '../lib/useFleet'
import { AuditRunDetailPage } from './-audit-run-detail-page'

export const Route = createFileRoute('/audit_/runs/$runId')({
  loader: async ({ params }) => {
    try {
      return {
        kind: 'audit' as const,
        data: await fetchRunDetail(params.runId),
      }
    } catch (auditError) {
      try {
        return {
          kind: 'fleet' as const,
          data: await fetchFleetSnapshotDetail(params.runId),
        }
      } catch {
        throw auditError
      }
    }
  },
  component: AuditRunDetailRoute,
  pendingComponent: AuditRunDetailPending,
  errorComponent: AuditRunDetailError,
})

function AuditRunDetailRoute() {
  const data = Route.useLoaderData()
  const { runId } = Route.useParams()
  return <AuditRunDetailPage data={data} runId={runId} />
}

// Local back link for the pending/error views. Kept out of the SQL-bearing
// `-audit-run-detail-page` sibling on purpose: importing anything shared across
// the component + pendingComponent + errorComponent split nodes would pin that
// sibling (and its transitive CodeMirror import) into this eager route reference
// module. So this module imports only `AuditRunDetailPage` (used by the single
// `component` node), which the code-splitter can relocate. [FIX-1 / Defect D-1]
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

// A skeleton of the report the route is about to show — header, tab strip and
// first card — rather than a spinner over an empty page. [E-47]
function AuditRunDetailPending() {
  return (
    <div className="space-y-6 w-full">
      <BackToHealthCheck />
      <VStack aria-busy="true" className="gap-6 items-stretch">
        <span className="sr-only">Loading saved run</span>
        <HStack className="gap-4 items-center">
          <Skeleton className="h-12 w-12 rounded-2xl" />
          <VStack className="gap-2 items-start">
            <Skeleton className="h-5 w-64 rounded-md" />
            <Skeleton className="h-3 w-80 max-w-full rounded-md" />
          </VStack>
        </HStack>
        <HStack className="gap-6 items-center">
          {[0, 1, 2].map((tab) => (
            <Skeleton key={tab} className="h-4 w-24 rounded-md" />
          ))}
        </HStack>
        <div className="rounded-2xl border border-border-layout-1 p-6">
          <VStack className="gap-3 items-stretch">
            <Skeleton className="h-4 w-48 rounded-md" />
            <Skeleton className="h-3 w-full rounded-md" />
            <Skeleton className="h-3 w-5/6 rounded-md" />
            <Skeleton className="h-3 w-2/3 rounded-md" />
          </VStack>
        </div>
      </VStack>
    </div>
  )
}

function AuditRunDetailError({ error }: { error: Error }) {
  const navigate = useNavigate()
  const missing = /\b404\b|not found/i.test(error.message ?? '')
  return (
    <div className="space-y-6 w-full">
      <BackToHealthCheck />
      <ErrorState
        errorClass="rdst-service"
        title={
          missing ? 'This run is no longer saved' : "This run couldn't be opened"
        }
        message={
          missing
            ? 'RDST has no report stored under this id. Saved runs are removed when local data is reset.'
            : 'RDST could not read the saved report for this run.'
        }
        trustworthy="Your other saved reports are unaffected."
        action={{
          label: 'Back to Reports',
          icon: 'chevron-left',
          onClick: () =>
            void navigate({
              to: '/audit',
              search: { tab: 'history' },
              hash: 'history',
            }),
        }}
        detail={error.message || undefined}
      />
    </div>
  )
}
