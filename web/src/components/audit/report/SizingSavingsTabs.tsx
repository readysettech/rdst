import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  formatMoney,
  formatPercent,
  formatReportNumber,
  VERDICT_LABELS,
} from '../../../lib/auditReportFormat'
import type { AuditReport } from '../../../types/audit'
import { TableHeaderCell } from '../../TableHeaderCell'
import { HealthReportSections } from './HealthSections'
import {
  InstanceClassValue,
  ReportEmptyState,
  SectionCard,
  StatCard,
} from './ReportPrimitives'

export function SizingTabContent({ report }: { report: AuditReport }) {
  const sizing = report.sizing
  const cpu = report.cloudwatch_cpu
  const hasSizingData =
    !!sizing &&
    Object.values(sizing).some(
      (value) => value !== null && value !== undefined && value !== ''
    )
  const hasCpuData =
    !!cpu &&
    [cpu.avg_cpu, cpu.max_cpu, cpu.min_cpu, cpu.hours].some(
      (value) => value !== null && value !== undefined
    )
  if (!hasSizingData && !hasCpuData) {
    return (
      <ReportEmptyState>
        No sizing data was collected for this run.
      </ReportEmptyState>
    )
  }
  const verdict =
    VERDICT_LABELS[sizing?.verdict || 'unknown'] || VERDICT_LABELS.unknown
  return (
    <VStack className="gap-6 items-stretch">
      <SectionCard icon="adjustment-horizontal" title="Right-Sizing Verdict">
        <div className="p-5 grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-4">
          <StatCard
            compact
            label="Verdict"
            value={verdict.label}
            badge={verdict}
          />
          <div className="bg-surface-layout-2/50 border border-border-layout-1 rounded-lg p-3">
            <VStack className="gap-1.5 items-start">
              <Text
                level="caption"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                Current instance
              </Text>
              <InstanceClassValue report={report} />
            </VStack>
          </div>
          <StatCard
            compact
            label="Suggested instance"
            value={sizing?.suggested_instance_class || 'No change recommended'}
          />
          <StatCard
            compact
            label="Estimated CPU"
            value={formatPercent(sizing?.estimated_cpu_pct)}
            hint={
              sizing?.concurrent_query_load != null
                ? `${formatReportNumber(sizing.concurrent_query_load)} concurrent queries`
                : undefined
            }
          />
        </div>
        {sizing?.explanation && (
          <div className="px-5 pb-5">
            <Text level="body-small" className="text-content-layout-2">
              {sizing.explanation}
            </Text>
          </div>
        )}
      </SectionCard>
      {hasCpuData ? (
        <HealthReportSections report={report} content="cpu" />
      ) : (
        <ReportEmptyState>
          No CloudWatch CPU evidence was collected for this run.
        </ReportEmptyState>
      )}
    </VStack>
  )
}

export function SavingsTabContent({ report }: { report: AuditReport }) {
  const sizing = report.sizing
  const hasSavings =
    sizing?.current_monthly_cost_usd != null ||
    sizing?.suggested_monthly_cost_usd != null ||
    sizing?.potential_savings_usd != null ||
    sizing?.readyset_projected_cost_usd != null ||
    sizing?.readyset_projected_savings_usd != null
  if (!hasSavings) {
    return (
      <ReportEmptyState>
        No savings or cost data was collected for this run.
      </ReportEmptyState>
    )
  }
  return (
    <VStack className="gap-6 items-stretch">
      <SectionCard
        icon="adjustment-horizontal"
        title="Projected Monthly Savings"
      >
        <div className="p-5 grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <StatCard
            label="Right-sizing savings"
            value={
              sizing?.potential_savings_usd != null
                ? `${formatMoney(sizing.potential_savings_usd)}/mo`
                : '-'
            }
            hint={
              sizing?.suggested_instance_class
                ? `Move to ${sizing.suggested_instance_class}`
                : 'No instance change was recommended'
            }
            valueClassName="text-content-positive-soft"
          />
          <StatCard
            label="Readyset caching-projected savings"
            value={
              sizing?.readyset_projected_savings_usd != null
                ? `${formatMoney(sizing.readyset_projected_savings_usd)}/mo`
                : '-'
            }
            hint={
              sizing?.readyset_projected_class
                ? `Projected instance: ${sizing.readyset_projected_class}`
                : 'Caching projection unavailable'
            }
            valueClassName="text-content-positive-soft"
          />
        </div>
      </SectionCard>
      <SectionCard icon="document-validation" title="Cost Breakdown">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-surface-layout-2/30">
                <TableHeaderCell>Scenario</TableHeaderCell>
                <TableHeaderCell>Instance class</TableHeaderCell>
                <TableHeaderCell align="right">Monthly cost</TableHeaderCell>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-layout-1">
              <tr>
                <td className="px-4 py-3 text-content-layout-2">Current</td>
                <td className="px-4 py-3 text-content-layout-2">
                  <InstanceClassValue report={report} />
                </td>
                <td className="px-4 py-3 text-right text-content-layout-2">
                  {formatMoney(sizing?.current_monthly_cost_usd)}/mo
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-content-layout-2">
                  Suggested right-sized
                </td>
                <td className="px-4 py-3 text-content-layout-2">
                  {sizing?.suggested_instance_class || '-'}
                </td>
                <td className="px-4 py-3 text-right text-content-layout-2">
                  {formatMoney(sizing?.suggested_monthly_cost_usd)}/mo
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-content-layout-2">
                  With Readyset caching
                </td>
                <td className="px-4 py-3 text-content-layout-2">
                  {sizing?.readyset_projected_class || '-'}
                </td>
                <td className="px-4 py-3 text-right text-content-layout-2">
                  {formatMoney(sizing?.readyset_projected_cost_usd)}/mo
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </VStack>
  )
}
