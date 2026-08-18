import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Card } from '@rs/ui-new/card-2'
import { Scrollable } from '@rs/ui-new/scrollable'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import { QueryCard } from '../../../components/QueryCard'
import { QueryCardFooter } from '../../../components/query-card/QueryCardFooter'
import { QueryCardHeader } from '../../../components/query-card/QueryCardHeader'
import { QueryCardSelectionIndicator } from '../../../components/query-card/QueryCardSelectionIndicator'
import { QueryCardSql } from '../../../components/query-card/QueryCardSql'
import { formatTimestamp } from '../../../lib/formatters'
import { isNotCacheable } from '../../../lib/queryImpact'
import type { Parameter } from '../../../lib/sqlParameters'

export function performanceParameterKey(ownerId: string, parameter: Parameter) {
  const suffix =
    parameter.placeholder === '?'
      ? `?${parameter.index}`
      : parameter.placeholder
  return `${ownerId}:${suffix}`
}

export function PerformanceQueryParameters({
  ownerId,
  inputPrefix,
  parameters,
  values,
  sources,
  onValueChange,
}: {
  ownerId: string
  inputPrefix: string
  parameters: Parameter[]
  values: Record<string, string>
  sources?: Record<string, string>
  onValueChange: (parameter: Parameter, value: string) => void
}) {
  if (parameters.length === 0) return null

  return (
    <div className="grid gap-3 border-t border-border-layout-soft px-4 py-4 tablet:grid-cols-2">
      {parameters.map((parameter) => {
        const key = performanceParameterKey(ownerId, parameter)
        const label =
          parameter.placeholder === '?'
            ? `Parameter ${parameter.index}`
            : parameter.placeholder

        return (
          <VStack key={key} className="items-stretch gap-1">
            <Text level="caption" className="text-content-layout-3">
              {label}
            </Text>
            <BaseInputText
              name={`${inputPrefix}-${key}`}
              value={values[key] ?? ''}
              placeholder="Enter a representative value"
              onChange={(event) => onValueChange(parameter, event.target.value)}
            />
            {sources?.[key] ? (
              <Text level="caption" className="text-content-positive-soft">
                {sources[key]}
              </Text>
            ) : null}
          </VStack>
        )
      })}
    </div>
  )
}

/**
 * Timestamped cacheability evidence for a selectable row: shown at point of
 * use, never a visibility gate (FB-13). Renders only for a confirmed
 * not-cacheable verdict; unknown or positive evidence asserts nothing here.
 */
export function PerformanceCacheabilityNote({
  readysetSupported,
  checkedAt,
}: {
  readysetSupported?: string
  checkedAt?: string
}) {
  if (!isNotCacheable(readysetSupported)) return null
  const when = checkedAt ? ` (${formatTimestamp(checkedAt)})` : ''
  return (
    <Text level="caption" className="text-content-layout-3">
      Last check: not cacheable{when}
    </Text>
  )
}

export function PerformanceQueryList({
  children,
  footer,
  ariaLabel,
}: {
  children: ReactNode
  footer?: ReactNode
  ariaLabel: string
}) {
  return (
    <section
      aria-label={ariaLabel}
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <Scrollable type="always" className="h-full">
        <VStack className="items-stretch gap-3 p-1 pr-5">{children}</VStack>
      </Scrollable>
      {footer}
    </section>
  )
}

function performanceCardTitle(title: string) {
  return (
    <Text level="label-medium" className="font-semibold text-content-layout-1">
      {title}
    </Text>
  )
}

function performanceCardBadges(parameterCount: number) {
  return parameterCount > 0 ? (
    <Tag
      size="small"
      variant="neutral"
      modifier="ghost"
      label={`${parameterCount} ${parameterCount === 1 ? 'parameter' : 'parameters'}`}
    />
  ) : undefined
}

export function PerformanceQueryCard({
  queryHash,
  selected,
  disabled = false,
  onSelect,
  title,
  sql,
  meta,
  parameterCount = 0,
  parameterContent,
}: {
  queryHash: string
  selected: boolean
  disabled?: boolean
  onSelect: () => void
  title: string
  sql: string
  meta?: ReactNode
  parameterCount?: number
  parameterContent?: ReactNode
}) {
  const titleContent = performanceCardTitle(title)
  const badges = performanceCardBadges(parameterCount)
  const selectionLabel = `${selected ? 'Deselect' : 'Select'} ${title}`

  if (!selected || !parameterContent) {
    return (
      <QueryCard
        sql={sql}
        truncateOneLine={!selected}
        title={titleContent}
        badges={badges}
        meta={meta}
        selectable
        selected={selected}
        selectionDisabled={disabled}
        selectionLabel={selectionLabel}
        onSelect={onSelect}
        data-query-hash={queryHash}
      />
    )
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect()
    }
  }

  return (
    <Card
      data-query-hash={queryHash}
      className="ring-2 ring-border-primary-soft shadow-elevation-2 transition-[box-shadow]"
    >
      <Card.Content className="p-0 overflow-hidden">
        {/* biome-ignore lint/a11y/useSemanticElements: a native button cannot contain the card's block-level header and SQL bands */}
        <div
          role="button"
          tabIndex={disabled ? -1 : 0}
          aria-pressed
          aria-disabled={disabled || undefined}
          aria-label={selectionLabel}
          onClick={disabled ? undefined : onSelect}
          onKeyDown={handleKeyDown}
          className={
            disabled
              ? 'cursor-not-allowed opacity-50'
              : 'cursor-pointer focus-visible:outline-none focus-visible:shadow-focus'
          }
        >
          <QueryCardHeader
            leading={<QueryCardSelectionIndicator selected />}
            title={titleContent}
            badges={badges}
          />
          <QueryCardSql sql={sql} expandable={false} copyable={false} />
        </div>
        {parameterContent}
      </Card.Content>
      <QueryCardFooter meta={meta} detailsOpen={false} />
    </Card>
  )
}
