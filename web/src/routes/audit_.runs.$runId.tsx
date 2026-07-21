/**
 * Standalone detail route for a saved audit/capture run.
 *
 * Past runs used to open inline inside /audit (component-local `loadedReport`
 * state, URL frozen at /audit — not deep-linkable, back did nothing). This route
 * gives each saved run its own URL: the Past Runs list links here, the loader
 * fetches the saved run, and browser back returns to the launcher.
 *
 * The report / workload body lives in the route-ignored `-audit-run-detail-page`
 * sibling (which imports the shared views from `-audit-views`) so the CodeMirror
 * SQL stack stays code-split — an exported page in this eager route reference
 * module would pin CodeMirror into the eager graph. [FIX-1 / Defect D-1;
 * feedback-triage-2 §1.3, USE-041/042/097]
 */

import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { createFileRoute, Link } from '@tanstack/react-router'
import { fetchRunDetail } from '../lib/useAudit'
import { AuditRunDetailPage } from './-audit-run-detail-page'

export const Route = createFileRoute('/audit_/runs/$runId')({
  loader: ({ params }) => fetchRunDetail(params.runId),
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

function AuditRunDetailPending() {
  return (
    <div className="space-y-6 w-full">
      <BackToHealthCheck />
      <HStack className="gap-3 items-center px-1 py-8">
        <Spinner size="base" />
        <Text level="body-small" className="text-content-layout-2">
          Loading saved run…
        </Text>
      </HStack>
    </div>
  )
}

function AuditRunDetailError({ error }: { error: Error }) {
  return (
    <div className="space-y-6 w-full">
      <BackToHealthCheck />
      <div className="px-5 py-4 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
        <VStack className="gap-1 items-start">
          <Text level="label-small" className="text-content-negative-soft">
            Couldn’t load this run
          </Text>
          <Text level="body-small" className="text-content-layout-2">
            {error.message || 'The saved run may have been removed.'}
          </Text>
        </VStack>
      </div>
    </div>
  )
}
