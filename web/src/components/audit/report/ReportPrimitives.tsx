/**
 * Shared building blocks for the audit report sections: tab strip, stat and
 * section cards, tables, and the controlled disclosure.
 */

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Card } from '@rs/ui-new/card'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useId } from 'react'
import { recordValue, severityVariant } from '../../../lib/auditReportFormat'
import type { AuditReport, HealthFinding } from '../../../types/audit'
import { TableHeaderCell } from '../../TableHeaderCell'

export function ReportTabs<T extends string>({
  label,
  tabs,
  selected,
  onSelect,
  nested = false,
  variant = 'primary',
  description,
}: {
  label: string
  tabs: Array<{ id: T; label: string }>
  selected?: T
  onSelect: (tab: T) => void
  nested?: boolean
  variant?: 'primary' | 'secondary'
  description?: string
}) {
  const secondary = variant === 'secondary'
  const tabLayoutId = useId()
  return (
    <div
      className={
        secondary
          ? 'bg-surface-layout-2/30'
          : `sticky z-20 bg-surface-layout-1/95 backdrop-blur border-b border-border-layout-1 ${
              nested ? 'top-12' : 'top-0'
            }`
      }
    >
      <TabList
        aria-label={label}
        className={`overflow-x-auto custom-scrollbar ${
          secondary ? 'gap-2 border-b-0 p-2' : 'gap-4 px-4'
        }`}
      >
        {tabs.map((tab) => (
          <TabItemButton
            key={tab.id}
            layoutPrefix={`report-${tabLayoutId}`}
            label={tab.label}
            active={selected === tab.id}
            onClick={() => onSelect(tab.id)}
            className={secondary ? 'h-10 shrink-0 px-3' : 'shrink-0'}
          />
        ))}
      </TabList>
      {description && (
        <div className="px-4 pb-3">
          <ReportTabDescription>{description}</ReportTabDescription>
        </div>
      )}
    </div>
  )
}

export function ReportTabDescription({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <Text level="body-small" className="text-content-layout-3 leading-relaxed">
      {children}
    </Text>
  )
}

export function ReportEmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 p-5">
      <Text level="body-small" className="text-content-layout-3">
        {children}
      </Text>
    </div>
  )
}

export function StatCard({
  label,
  value,
  hint,
  badge,
  valueClassName,
  compact = false,
}: {
  label: string
  value: string
  hint?: string
  badge?: {
    label: string
    variant: 'negative' | 'warning' | 'positive' | 'informative'
  }
  valueClassName?: string
  compact?: boolean
}) {
  return (
    <div
      className={`bg-surface-layout-2/50 border border-border-layout-1 ${
        compact ? 'rounded-lg p-3 min-h-0' : 'rounded-xl p-5 min-h-32'
      }`}
    >
      <VStack className={compact ? 'gap-1.5 items-start' : 'gap-3 items-start'}>
        <Text
          level="caption"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          {label}
        </Text>
        <HStack className="gap-2 items-center flex-wrap">
          <Text
            level={compact ? 'label-medium' : 'headline-5'}
            className={`${valueClassName ?? 'text-content-layout-1'} tabular-nums`}
          >
            {value}
          </Text>
          {badge && (
            <Tag
              size="small"
              variant={badge.variant}
              modifier="ghost"
              label={badge.label}
            />
          )}
        </HStack>
        {hint && (
          <Text level="caption" className="text-content-layout-3">
            {hint}
          </Text>
        )}
      </VStack>
    </div>
  )
}

export function SectionCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName
  title: string
  children: React.ReactNode
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-6 py-4 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center">
            <Icon
              name={icon}
              label=""
              aria-hidden="true"
              className="w-4 h-4 text-content-layout-3"
            />
            <Text
              level="overline"
              className="text-content-layout-3 uppercase tracking-wider"
            >
              {title}
            </Text>
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  )
}

/**
 * A quiet, secondary metric card for the report's supporting-scores row —
 * lighter than a SectionCard, lets each card compose its own body. Sits at
 * content-layout weight so the raised verdict card stays the one focal point
 * (VIS-011, VIS-017).
 */
export function SupportingCard({
  icon,
  title,
  children,
}: {
  icon: IconStrokeName
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="bg-surface-layout-1 rounded-xl p-5 min-h-44 border border-border-layout-1">
      <VStack className="gap-4 items-start">
        <HStack className="gap-2 items-center">
          <Icon
            name={icon}
            label={title}
            className="w-3.5 h-3.5 text-content-layout-3"
          />
          <Text
            level="overline"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            {title}
          </Text>
        </HStack>
        {children}
      </VStack>
    </div>
  )
}

/**
 * Instance-class honesty: a missing class renders as a muted "Unknown" with a
 * tooltip (never a fabricated type), and an estimated class carries an
 * explicit "estimated" pill next to the value.
 */
export function InstanceClassValue({ report }: { report: AuditReport }) {
  if (!report.instance_class) {
    return (
      <span>
        <Text as="span" level="caption" className="text-content-layout-3">
          Not sure what instance this is running on
        </Text>
      </span>
    )
  }
  return (
    <HStack className="gap-1.5 items-center">
      <Text as="span" level="mono-small" className="text-content-layout-2">
        {report.instance_class}
      </Text>
      {report.instance_class_source === 'estimated' && (
        <Tag size="small" variant="muted" modifier="ghost" label="estimated" />
      )}
    </HStack>
  )
}

export function FindingsList({ findings }: { findings: HealthFinding[] }) {
  return (
    // gap-4 BETWEEN findings > gap-3 WITHIN a finding row (§1 grouping).
    <VStack className="gap-4 items-stretch">
      {findings.map((finding, index) => (
        <HStack key={index} className="gap-3 items-start">
          <Tag
            size="small"
            variant={severityVariant(finding.severity)}
            modifier="ghost"
            label={(finding.severity || 'info').toUpperCase()}
          />
          <VStack className="gap-0.5 items-start min-w-0">
            <Text level="label-medium" className="text-content-layout-1">
              {finding.title || 'Finding'}
            </Text>
            {finding.body && (
              <Text level="body-small" className="text-content-layout-2">
                {finding.body}
              </Text>
            )}
          </VStack>
        </HStack>
      ))}
    </VStack>
  )
}

export function SimpleTable({
  columns,
  rows,
}: {
  columns: Array<{
    key: string
    label: string
    render?: (row: Record<string, unknown>) => React.ReactNode
  }>
  rows: Record<string, unknown>[]
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-surface-layout-2/30">
            {columns.map((column) => (
              <TableHeaderCell key={column.key}>{column.label}</TableHeaderCell>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-layout-1">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className="px-4 py-2 text-content-layout-2 align-top"
                >
                  {column.render
                    ? column.render(row)
                    : recordValue(row, column.key)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Controlled disclosure rather than native <details>: a native disclosure
// expanding inside the app shell's scroll viewport leaves stale, compressed
// scroll geometry, which collapses the report into a fraction of the page.
export function DetailDisclosure({
  label,
  count,
  children,
}: {
  label: string
  count: number
  children: React.ReactNode
}) {
  return (
    <Disclosure
      title={`${label} (${count})`}
      className="rounded-none border-x-0 border-b-0"
      panelClassName="p-0"
    >
      {children}
    </Disclosure>
  )
}
