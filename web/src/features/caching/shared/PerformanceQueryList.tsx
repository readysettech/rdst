import { cn } from '@rs/tailwind-base'
import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Scrollable } from '@rs/ui-new/scrollable'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'
import { SqlTokens } from '../../../components/SqlTokens'
import { collapseWhitespace } from '../../../lib/collapseWhitespace'
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
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border-layout-soft"
    >
      <Scrollable type="always" className="h-full">
        {children}
      </Scrollable>
      {footer}
    </section>
  )
}

export function PerformanceQueryRow({
  id,
  checked,
  disabled = false,
  onCheckedChange,
  title,
  sql,
  meta,
  children,
}: {
  id: string
  checked: boolean
  disabled?: boolean
  onCheckedChange: () => void
  title: string
  sql: string
  meta?: ReactNode
  children?: ReactNode
}) {
  return (
    <div
      className={cn(
        'border-b border-border-layout-soft last:border-0',
        checked && 'bg-surface-primary-soft/20',
        disabled && 'opacity-50'
      )}
    >
      <label
        htmlFor={id}
        className={cn(
          'flex items-start gap-3 px-4 py-4',
          disabled ? 'cursor-not-allowed' : 'cursor-pointer'
        )}
      >
        <BaseInputCheckbox
          id={id}
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
          className="mt-0.5"
        />
        <VStack className="min-w-0 flex-1 items-start gap-1">
          <Text
            level="label-small"
            className="w-full truncate text-content-layout-1"
          >
            {title}
          </Text>
          <div className="w-full min-w-0 overflow-hidden">
            <SqlTokens
              sql={collapseWhitespace(sql)}
              title={sql}
              className="truncate whitespace-nowrap text-mono-small"
            />
          </div>
          {meta}
        </VStack>
      </label>
      {checked ? children : null}
    </div>
  )
}
