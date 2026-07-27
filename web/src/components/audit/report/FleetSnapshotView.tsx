import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect } from 'react'
import { formatMoney, formatScore } from '../../../lib/auditReportFormat'
import type { FleetReportTab } from '../../../lib/auditReportLocation'
import {
  currentSearchParams,
  FLEET_REPORT_TAB_DESCRIPTIONS,
  FLEET_REPORT_TABS,
  updateReportLocation,
  useReportLocationVersion,
} from '../../../lib/auditReportLocation'
import { analysisItemText } from '../../../lib/auditReportModel'
import type { FleetSnapshotDetail } from '../../../lib/useFleet'
import type { HealthFinding } from '../../../types/audit'
import { TableHeaderCell } from '../../TableHeaderCell'
import { FleetTargetDetail } from './AuditReportView'
import {
  FindingsList,
  ReportEmptyState,
  ReportTabs,
  SectionCard,
  StatCard,
} from './ReportPrimitives'

/** A saved fleet snapshot reopened from Reports with full per-target details. */
export function FleetSnapshotView({
  detail,
  aiCredentialInvalid,
}: {
  detail: FleetSnapshotDetail
  aiCredentialInvalid: boolean
}) {
  const results = detail.results ?? []
  const failures =
    detail.targets_failed ?? results.filter((result) => !!result.error).length
  const successes = detail.targets_audited - failures
  const insights = detail.fleet_insights || {}
  const findings = (insights.top_findings ||
    insights.fleet_findings ||
    insights.findings) as unknown
  const nextSteps = (insights.next_steps ||
    insights.recommended_actions) as unknown
  const healthScore =
    typeof insights.health_score === 'number'
      ? insights.health_score
      : undefined
  const healthLabel =
    typeof insights.health_label === 'string'
      ? insights.health_label
      : undefined
  const healthVariant =
    healthScore == null
      ? 'informative'
      : healthScore >= 75
        ? 'positive'
        : healthScore >= 60
          ? 'warning'
          : 'negative'
  const fleetFindings: HealthFinding[] = Array.isArray(findings)
    ? findings.map((finding) => {
        if (typeof finding === 'string')
          return { severity: 'info', title: finding }
        if (finding && typeof finding === 'object') {
          const item = finding as Record<string, unknown>
          return {
            severity:
              typeof item.severity === 'string' ? item.severity : 'info',
            title: typeof item.title === 'string' ? item.title : 'Finding',
            body: typeof item.body === 'string' ? item.body : undefined,
          }
        }
        return { severity: 'info', title: String(finding ?? 'Finding') }
      })
    : []
  const sizingTotals = results.reduce(
    (totals, result) => {
      if (result.error) return totals
      const sizing = result.sizing
      const current = sizing?.current_monthly_cost_usd
      if (current == null) return totals
      const suggested = sizing?.suggested_monthly_cost_usd ?? current
      totals.targets += 1
      totals.current += current
      totals.suggested += suggested
      totals.savings +=
        sizing?.potential_savings_usd ?? Math.max(0, current - suggested)
      if (sizing?.verdict === 'oversized') totals.oversized += 1
      return totals
    },
    { targets: 0, current: 0, suggested: 0, savings: 0, oversized: 0 }
  )
  const fleetCurrentCost = detail.total_monthly_cost_usd ?? sizingTotals.current
  const fleetPotentialSavings =
    detail.potential_savings_usd ?? sizingTotals.savings
  const fleetSuggestedCost =
    sizingTotals.targets > 0
      ? sizingTotals.suggested
      : Math.max(0, fleetCurrentCost - fleetPotentialSavings)
  const hasFleetSizing =
    sizingTotals.targets > 0 ||
    detail.total_monthly_cost_usd != null ||
    detail.potential_savings_usd != null
  const locationVersion = useReportLocationVersion()
  const params = currentSearchParams(locationVersion)
  const requestedFleetTab = params.get('fleetTab')
  const requestedTarget = params.get('target')
  const targetIndex = results.findIndex(
    (result) => (result.target_name || result.host || '') === requestedTarget
  )
  const selectedOuterTab =
    (requestedFleetTab === 'target' ||
      (!requestedFleetTab && targetIndex >= 0)) &&
    targetIndex >= 0
      ? `target-${targetIndex}`
      : requestedFleetTab === 'sizing'
        ? 'sizing'
        : 'summary'
  const selectedFleetTab: FleetReportTab | undefined =
    selectedOuterTab === 'summary' || selectedOuterTab === 'sizing'
      ? selectedOuterTab
      : undefined
  const instanceTabs = results.map((result, index) => ({
    id: `target-${index}`,
    label: result.target_name || result.host || `Target ${index + 1}`,
  }))
  const selectedInstanceTab = selectedOuterTab.startsWith('target-')
    ? selectedOuterTab
    : undefined

  useEffect(() => {
    if (requestedFleetTab || typeof window === 'undefined') return
    const match = window.location.hash.match(/-target-(\d+)$/)
    const index = match ? Number(match[1]) - 1 : -1
    const result = results[index]
    if (!result) return
    updateReportLocation(
      {
        fleetTab: 'target',
        target: result.target_name || result.host || `Target ${index + 1}`,
        tab: 'overview',
        queryTab: undefined,
      },
      { replace: true }
    )
  }, [requestedFleetTab, results])

  const selectOuterTab = (tab: string) => {
    if (tab === 'summary' || tab === 'sizing') {
      updateReportLocation({
        fleetTab: tab,
        target: undefined,
        tab: undefined,
        queryTab: undefined,
      })
      return
    }
    const index = Number(tab.replace('target-', ''))
    const result = results[index]
    if (!result) return
    updateReportLocation({
      fleetTab: 'target',
      target: result.target_name || result.host || `Target ${index + 1}`,
      tab: 'overview',
      queryTab: undefined,
    })
  }

  return (
    <VStack className="gap-6 items-stretch">
      <ReportTabs
        label="Fleet report sections"
        tabs={FLEET_REPORT_TABS}
        selected={selectedFleetTab}
        onSelect={selectOuterTab}
        description={
          selectedFleetTab
            ? FLEET_REPORT_TAB_DESCRIPTIONS[selectedFleetTab]
            : undefined
        }
      />
      {instanceTabs.length > 0 && (
        <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border-layout-1">
            <Text
              as="h2"
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              Instances
            </Text>
          </div>
          <ReportTabs
            label="Instances"
            tabs={instanceTabs}
            selected={selectedInstanceTab}
            onSelect={selectOuterTab}
            variant="secondary"
          />
        </div>
      )}
      {selectedOuterTab === 'summary' && (
        <SectionCard icon="document-validation" title="Fleet Summary">
          <div className="p-6 grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-5 gap-5">
            <StatCard
              label="Fleet health"
              value={formatScore(healthScore)}
              badge={
                healthLabel
                  ? { label: healthLabel.toUpperCase(), variant: healthVariant }
                  : undefined
              }
            />
            <StatCard
              label="Targets audited"
              value={`${detail.targets_audited}`}
            />
            <StatCard label="Successful targets" value={`${successes}`} />
            <StatCard label="Failed targets" value={`${failures}`} />
            <StatCard
              label="Avg cache opportunity"
              value={formatScore(detail.avg_cache_opportunity)}
            />
          </div>
          {typeof insights.executive_summary === 'string' && (
            <div className="px-6 pb-6 max-w-4xl">
              <div className="rounded-xl bg-surface-layout-2/50 border border-border-layout-1 p-4">
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider block mb-2"
                >
                  Executive summary
                </Text>
                <Text
                  level="body-small"
                  className="text-content-layout-2 leading-relaxed"
                >
                  {insights.executive_summary}
                </Text>
              </div>
            </div>
          )}
          {fleetFindings.length > 0 && (
            <div className="px-6 pb-6">
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider block mb-3"
              >
                Top findings
              </Text>
              <FindingsList findings={fleetFindings} />
            </div>
          )}
          {Array.isArray(nextSteps) && nextSteps.length > 0 && (
            <div className="mx-6 mb-6 rounded-xl border border-border-layout-1 bg-surface-layout-2/30 p-4">
              <VStack className="gap-4 items-stretch">
                <Text level="label-small" className="text-content-layout-1">
                  Next steps
                </Text>
                {nextSteps.map((step, index) => (
                  <HStack
                    key={index}
                    className="gap-3 items-start border-t border-border-layout-1 pt-3 first:border-t-0 first:pt-0"
                  >
                    <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center shrink-0">
                      <Text
                        level="caption"
                        className="text-content-primary-soft font-semibold"
                      >
                        {index + 1}
                      </Text>
                    </div>
                    <Text
                      level="body-small"
                      className="text-content-layout-2 min-w-0 leading-relaxed"
                    >
                      {analysisItemText(step)}
                    </Text>
                  </HStack>
                ))}
              </VStack>
            </div>
          )}
          {typeof insights.error === 'string' && (
            <div className="px-5 pb-5">
              <Text level="body-small" className="text-content-negative-soft">
                Fleet analysis failed: {insights.error}
              </Text>
            </div>
          )}
          {typeof insights.raw === 'string' && (
            <div className="px-5 pb-5">
              <Text
                level="body-small"
                className="text-content-layout-2 whitespace-pre-wrap"
              >
                {insights.raw}
              </Text>
            </div>
          )}
        </SectionCard>
      )}
      {selectedOuterTab === 'sizing' &&
        (hasFleetSizing ? (
          <VStack className="gap-6 items-stretch">
            <SectionCard
              icon="adjustment-horizontal"
              title="Fleet Sizing Summary"
            >
              <div className="p-6 grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-5">
                <StatCard
                  label="Current cluster total"
                  value={`${formatMoney(fleetCurrentCost)}/mo`}
                />
                <StatCard
                  label="Suggested cluster total"
                  value={`${formatMoney(fleetSuggestedCost)}/mo`}
                />
                <StatCard
                  label="Total potential cluster savings"
                  value={`${formatMoney(fleetPotentialSavings)}/mo`}
                />
                <StatCard
                  label="Oversized nodes"
                  value={`${sizingTotals.oversized} / ${sizingTotals.targets}`}
                />
              </div>
              <div className="px-6 pb-6">
                <Text level="body-small" className="text-content-layout-2">
                  Across the cluster
                  {sizingTotals.targets > 0
                    ? ` (${sizingTotals.targets} costed nodes)`
                    : ''}
                  , right-sizing could save approximately{' '}
                  <span className="text-content-positive-soft">
                    {formatMoney(fleetPotentialSavings)}/mo
                  </span>
                  ; {sizingTotals.oversized}{' '}
                  {sizingTotals.oversized === 1 ? 'node is' : 'nodes are'}{' '}
                  oversized.
                </Text>
              </div>
            </SectionCard>
            <SectionCard icon="database" title="Per-Node Sizing Rollup">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px]">
                  <thead>
                    <tr className="bg-surface-layout-2/30">
                      <TableHeaderCell>Target</TableHeaderCell>
                      <TableHeaderCell>Current class</TableHeaderCell>
                      <TableHeaderCell>Suggested class</TableHeaderCell>
                      <TableHeaderCell align="right">
                        Current monthly
                      </TableHeaderCell>
                      <TableHeaderCell align="right">
                        Suggested monthly
                      </TableHeaderCell>
                      <TableHeaderCell align="right">Savings</TableHeaderCell>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-layout-1">
                    {results
                      .filter(
                        (result) =>
                          !result.error &&
                          result.sizing?.current_monthly_cost_usd != null
                      )
                      .map((result, index) => (
                        <tr key={`${result.target_name}-${index}`}>
                          <td className="px-4 py-3 text-content-layout-2">
                            {result.target_name ||
                              result.host ||
                              `Target ${index + 1}`}
                          </td>
                          <td className="px-4 py-3 text-content-layout-2">
                            {result.instance_class || 'Unknown'}
                          </td>
                          <td className="px-4 py-3 text-content-layout-2">
                            {result.sizing?.suggested_instance_class ||
                              'No change'}
                          </td>
                          <td className="px-4 py-3 text-right text-content-layout-2">
                            {formatMoney(
                              result.sizing?.current_monthly_cost_usd
                            )}
                          </td>
                          <td className="px-4 py-3 text-right text-content-layout-2">
                            {formatMoney(
                              result.sizing?.suggested_monthly_cost_usd ??
                                result.sizing?.current_monthly_cost_usd
                            )}
                          </td>
                          <td className="px-4 py-3 text-right text-content-positive-soft">
                            {formatMoney(result.sizing?.potential_savings_usd)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          </VStack>
        ) : (
          <ReportEmptyState>
            No fleet sizing data was collected for this run.
          </ReportEmptyState>
        ))}
      {selectedOuterTab.startsWith('target-') &&
        (() => {
          const index = Number(selectedOuterTab.replace('target-', ''))
          const result = results[index]
          if (!result) return null
          return (
            <div role="tabpanel">
              <FleetTargetDetail
                result={result}
                aiCredentialInvalid={aiCredentialInvalid}
              />
            </div>
          )
        })()}
      {results.length === 0 && selectedOuterTab === 'summary' && (
        <ReportEmptyState>
          This snapshot has no per-target results to show.
        </ReportEmptyState>
      )}
    </VStack>
  )
}
