/**
 * Modal for entering parameter values for parameterized queries
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
} from '../parameterHighlighting'
import { SQLDisplay } from '../SQLDisplay'

interface ParameterDialogProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (substitutedQuery: string) => void
  query: string
  initialValues?: Record<string, string | number>
}

interface Parameter {
  placeholder: string
  index: number
  type: 'positional' | 'named'
}

/**
 * Detect parameters in a SQL query
 * Supports: $1, $2 (PostgreSQL), ? (MySQL), :name, @name (named)
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

  // MySQL positional: ? (we number them by occurrence)
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

  // Sort by index for positional params
  return params.sort((a, b) => a.index - b.index)
}

/**
 * Check if a query has parameters
 */
export function hasParameters(sql: string): boolean {
  return detectParameters(sql).length > 0
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

  // Handle MySQL ? parameters (replace in order)
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
    // Named params like :p1 or @p1 — backend key is without the prefix
    backendKey = param.placeholder.replace(/^[:@]/, '')
  } else if (param.placeholder.startsWith('$')) {
    // PostgreSQL $1 -> backend key p1
    backendKey = param.placeholder.replace('$', 'p')
  } else {
    // MySQL ? -> backend key p{index}
    backendKey = `p${param.index}`
  }
  const value = storedParams[backendKey]
  return value != null ? String(value) : ''
}

export function ParameterDialog({
  isOpen,
  onClose,
  onSubmit,
  query,
  initialValues,
}: ParameterDialogProps) {
  const parameters = useMemo(() => detectParameters(query), [query])
  const parameterHighlights = useMemo(
    () =>
      buildParameterHighlights(
        parameters.map((parameter) => parameter.placeholder)
      ),
    [parameters]
  )
  const colorByPlaceholder = useMemo(
    () =>
      new Map<string, number>(
        parameterHighlights.map((highlight) => [
          highlight.token,
          highlight.colorIndex,
        ])
      ),
    [parameterHighlights]
  )
  const [values, setValues] = useState<Record<string, string>>({})

  useEffect(() => {
    const init: Record<string, string> = {}
    parameters.forEach((p) => {
      const key = p.placeholder === '?' ? `?${p.index}` : p.placeholder
      init[key] = resolveInitialValue(p, initialValues)
    })
    setValues(init)
  }, [query, parameters, initialValues])

  const handleValueChange = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  const handleSubmit = () => {
    const substituted = substituteParameters(query, parameters, values)
    onSubmit(substituted)
  }

  const previewQuery = useMemo(() => {
    return substituteParameters(query, parameters, values)
  }, [query, parameters, values])

  const allFilled = Object.values(values).every((v) => v.trim() !== '')

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="p-0 gap-0">
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
                  {parameters.length} parameter
                  {parameters.length !== 1 ? 's' : ''} detected
                </Text>
              </div>
            </div>
          </div>

          {/* Content */}
          <div className="p-5 space-y-5 max-h-[60vh] overflow-auto">
            {/* Original Query */}
            <div>
              <Text
                as="label"
                level="label-small"
                className="text-content-layout-3 uppercase tracking-wider block mb-2"
              >
                Original Query
              </Text>
              <div className="bg-surface-layout-1 rounded-lg p-3 max-h-24 overflow-auto border border-border-layout-1">
                <SQLDisplay
                  sql={query}
                  wrap
                  parameterHighlights={parameterHighlights}
                />
              </div>
            </div>

            {/* Parameter Inputs */}
            <div>
              <Text
                as="label"
                level="label-small"
                className="text-content-layout-3 uppercase tracking-wider block mb-3"
              >
                Parameters
              </Text>
              <div className="space-y-3">
                {parameters.map((param) => {
                  const key =
                    param.placeholder === '?'
                      ? `?${param.index}`
                      : param.placeholder
                  const colorIndex =
                    colorByPlaceholder.get(param.placeholder) ?? 0
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
                          name={`param-${key}`}
                          value={values[key] || ''}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                            handleValueChange(key, e.target.value)
                          }
                          placeholder="Enter value"
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
              <Text level="body-small" className="text-content-layout-3 mt-2">
                Strings are automatically quoted. Numbers, NULL, TRUE, FALSE are
                passed as-is.
              </Text>
            </div>

            {/* Preview */}
            <Show when={Object.values(values).some((v) => v.trim() !== '')}>
              <div>
                <Text
                  as="label"
                  level="label-small"
                  className="text-content-layout-3 uppercase tracking-wider block mb-2"
                >
                  Preview
                </Text>
                <div className="bg-surface-positive-soft/30 rounded-lg p-3 max-h-24 overflow-auto border border-border-positive/30">
                  <SQLDisplay sql={previewQuery} wrap />
                </div>
              </div>
            </Show>
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
              label="Analyze Query"
              icon="speedometer"
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
