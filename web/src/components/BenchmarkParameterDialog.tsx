/**
 * Modal for entering parameter values for multiple parameterized queries (benchmark)
 */

import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Modal, ModalContent, ModalContentContainer } from '@rs/ui-new/modal'
import { Show } from '@rs/ui-new/show'
import { Text } from '@rs/ui-new/text'
import { useEffect, useMemo, useState } from 'react'
import {
  buildParameterHighlights,
  getParameterColor,
} from './parameterHighlighting'
import { SQLDisplay } from './SQLDisplay'

interface QueryWithParams {
  identifier: string // tag or hash
  sql: string
  name: string
  mostRecentParams?: Record<string, string | number>
}

interface BenchmarkParameterDialogProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (
    substitutedQueries: Array<{ identifier: string; sql: string }>
  ) => void
  queries: QueryWithParams[]
}

interface Parameter {
  placeholder: string
  index: number
  type: 'positional' | 'named'
}

/**
 * Detect parameters in a SQL query
 */
function detectParameters(sql: string): Parameter[] {
  const params: Parameter[] = []
  const seen = new Set<string>()

  // PostgreSQL positional: $1, $2, etc.
  const pgMatches = sql.matchAll(/\$(\d+)/g)
  for (const match of pgMatches) {
    const placeholder = match[0]
    if (!seen.has(placeholder)) {
      seen.add(placeholder)
      params.push({
        placeholder,
        index: Number.parseInt(match[1], 10),
        type: 'positional',
      })
    }
  }

  // MySQL positional: ?
  let questionIndex = 1
  const mysqlMatches = sql.matchAll(/\?/g)
  for (const _ of mysqlMatches) {
    params.push({
      placeholder: '?',
      index: questionIndex,
      type: 'positional',
    })
    questionIndex++
  }

  // Named parameters: :name or @name (but not ::type casts)
  const namedMatches = sql.matchAll(/(?<!:)[:@]([a-zA-Z_][a-zA-Z0-9_]*)/g)
  for (const match of namedMatches) {
    const placeholder = match[0]
    if (!seen.has(placeholder)) {
      seen.add(placeholder)
      params.push({
        placeholder,
        index: params.length + 1,
        type: 'named',
      })
    }
  }

  return params.sort((a, b) => a.index - b.index)
}

/**
 * Check if a query has parameters
 */
export function hasParameters(sql: string): boolean {
  return detectParameters(sql).length > 0
}

/**
 * Format a value for SQL substitution
 */
function formatValue(value: string): string {
  const trimmed = value.trim()

  if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
    return trimmed
  }

  if (['NULL', 'TRUE', 'FALSE'].includes(trimmed.toUpperCase())) {
    return trimmed.toUpperCase()
  }

  if (
    (trimmed.startsWith("'") && trimmed.endsWith("'")) ||
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
  ) {
    return trimmed
  }

  return `'${trimmed.replace(/'/g, "''")}'`
}

/**
 * Substitute parameter values into a query
 */
function substituteParameters(
  sql: string,
  params: Parameter[],
  values: Record<string, string>
): string {
  let result = sql

  // Handle MySQL ? parameters
  const questionParams = params.filter((p) => p.placeholder === '?')
  if (questionParams.length > 0) {
    const parts: string[] = []
    let lastIndex = 0
    let qIndex = 0

    for (let i = 0; i < result.length; i++) {
      if (result[i] === '?') {
        parts.push(result.slice(lastIndex, i))
        const value = values[`?${qIndex + 1}`] || ''
        parts.push(formatValue(value))
        lastIndex = i + 1
        qIndex++
      }
    }
    parts.push(result.slice(lastIndex))
    result = parts.join('')
  }

  // Handle PostgreSQL $N and named parameters
  for (const param of params) {
    if (param.placeholder !== '?') {
      const value = values[param.placeholder] || ''
      const escapedPlaceholder = param.placeholder.replace(
        /[.*+?^${}()|[\]\\]/g,
        '\\$&'
      )
      result = result.replace(
        new RegExp(escapedPlaceholder, 'g'),
        formatValue(value)
      )
    }
  }

  return result
}

/**
 * Resolve an initial value from backend's most_recent_params for a given parameter.
 * Backend stores params as {p1: value, p2: value, ...} (SQLGlot-normalized keys).
 */
function resolveInitialValue(
  param: Parameter,
  storedParams: Record<string, string | number> | undefined
): string {
  if (!storedParams) return ''
  let backendKey: string
  if (param.type === 'named') {
    backendKey = param.placeholder.replace(/^[:@]/, '')
  } else if (param.placeholder.startsWith('$')) {
    backendKey = param.placeholder.replace('$', 'p')
  } else {
    backendKey = `p${param.index}`
  }
  const value = storedParams[backendKey]
  return value != null ? String(value) : ''
}

export function BenchmarkParameterDialog({
  isOpen,
  onClose,
  onSubmit,
  queries,
}: BenchmarkParameterDialogProps) {
  // Track parameters and values for each query
  const queryParams = useMemo(() => {
    return queries.map((q) => {
      const parameters = detectParameters(q.sql)
      const parameterHighlights = buildParameterHighlights(
        parameters.map((parameter) => parameter.placeholder)
      )
      return {
        ...q, // spreads identifier, sql, name, and mostRecentParams
        parameters,
        parameterHighlights,
        colorByPlaceholder: new Map<string, number>(
          parameterHighlights.map((highlight) => [
            highlight.token,
            highlight.colorIndex,
          ])
        ),
      }
    })
  }, [queries])

  // Values keyed by "identifier:placeholder"
  const [values, setValues] = useState<Record<string, string>>({})

  // Initialize values when queries change, using stored params if available
  useEffect(() => {
    const init: Record<string, string> = {}
    for (const q of queryParams) {
      for (const p of q.parameters) {
        const key =
          p.placeholder === '?'
            ? `${q.identifier}:?${p.index}`
            : `${q.identifier}:${p.placeholder}`
        init[key] = resolveInitialValue(p, q.mostRecentParams)
      }
    }
    setValues(init)
  }, [queryParams])

  const handleValueChange = (
    queryId: string,
    placeholder: string,
    value: string
  ) => {
    const key = `${queryId}:${placeholder}`
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  const handleSubmit = () => {
    const substituted = queryParams.map((q) => {
      const queryValues: Record<string, string> = {}
      for (const p of q.parameters) {
        const key =
          p.placeholder === '?'
            ? `${q.identifier}:?${p.index}`
            : `${q.identifier}:${p.placeholder}`
        const paramKey = p.placeholder === '?' ? `?${p.index}` : p.placeholder
        queryValues[paramKey] = values[key] || ''
      }
      return {
        identifier: q.identifier,
        sql: substituteParameters(q.sql, q.parameters, queryValues),
      }
    })
    onSubmit(substituted)
  }

  // Check if all parameters are filled
  const allFilled = useMemo(() => {
    for (const q of queryParams) {
      for (const p of q.parameters) {
        const key =
          p.placeholder === '?'
            ? `${q.identifier}:?${p.index}`
            : `${q.identifier}:${p.placeholder}`
        if (!values[key]?.trim()) {
          return false
        }
      }
    }
    return true
  }, [queryParams, values])

  const totalParams = queryParams.reduce(
    (sum, q) => sum + q.parameters.length,
    0
  )

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="large" className="p-0 gap-0">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-border-layout-1 bg-surface-layout-1">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-surface-primary-soft">
                <Icon
                  name="edit"
                  label="Parameters"
                  size="base"
                  className="text-content-primary-soft"
                />
              </div>
              <div>
                <Text
                  as="h2"
                  level="headline-5"
                  className="text-content-layout-1"
                >
                  Enter Parameter Values
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {queries.length} queries, {totalParams} parameters total
                </Text>
              </div>
            </div>
          </div>

          {/* Content */}
          <div className="p-5 space-y-6 max-h-[60vh] overflow-auto">
            {queryParams.map((q) => (
              <div key={q.identifier} className="space-y-3">
                {/* Query header */}
                <div className="flex items-center gap-2">
                  <Text level="label-small" className="text-content-layout-1">
                    {q.name}
                  </Text>
                  <Text level="mono-small" className="text-content-layout-3">
                    {q.identifier.slice(0, 8)}
                  </Text>
                </div>

                {/* Original SQL */}
                <div className="bg-surface-layout-1 rounded-lg p-3 max-h-40 overflow-auto border border-border-layout-1">
                  <SQLDisplay
                    sql={q.sql}
                    wrap
                    parameterHighlights={q.parameterHighlights}
                  />
                </div>

                {/* Parameter inputs */}
                <Show when={q.parameters.length > 0}>
                  <div className="space-y-2 pl-4 border-l-2 border-border-layout-2">
                    {q.parameters.map((param) => {
                      const key =
                        param.placeholder === '?'
                          ? `?${param.index}`
                          : param.placeholder
                      const valueKey = `${q.identifier}:${key}`
                      const colorIndex =
                        q.colorByPlaceholder.get(param.placeholder) ?? 0
                      const color = getParameterColor(colorIndex)
                      return (
                        <div key={key} className="flex items-center gap-3">
                          <div className="w-14 flex-shrink-0 text-right">
                            <span
                              className="inline-block px-2 py-1 rounded border font-mono text-sm"
                              style={{
                                backgroundColor: color.badgeBackground,
                                borderColor: color.badgeBorder,
                                color: color.badgeText,
                              }}
                            >
                              {key}
                            </span>
                          </div>
                          <div className="flex-1">
                            <BaseInputText
                              name={`param-${valueKey}`}
                              value={values[valueKey] || ''}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) =>
                                handleValueChange(
                                  q.identifier,
                                  key,
                                  e.target.value
                                )
                              }
                              placeholder="Enter value"
                            />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </Show>
              </div>
            ))}

            <Text level="body-small" className="text-content-layout-3">
              Strings are automatically quoted. Numbers, NULL, TRUE, FALSE are
              passed as-is.
            </Text>
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-3 px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
            <Button
              variant="primary"
              modifier="ghost"
              label="Cancel"
              onClick={onClose}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Start Benchmark"
              icon="play"
              iconPosition="left"
              onClick={handleSubmit}
              disabled={!allFilled}
            />
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
