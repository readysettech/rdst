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
import {
  detectParameters,
  hasParameters,
  substituteParameters,
  resolveInitialValue,
} from '../lib/sqlParameters'

export { hasParameters }

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

const MANY_PARAMS_THRESHOLD = 8

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
        ...q,
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

  const [values, setValues] = useState<Record<string, string>>({})

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
  const hasMany = totalParams >= MANY_PARAMS_THRESHOLD
  const [expandedQueries, setExpandedQueries] = useState<Set<string>>(() => new Set())
  const toggleQueryExpanded = (id: string) => {
    setExpandedQueries((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size={hasMany ? 'extra-large' : 'large'} className="p-0 gap-0">
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
                {/* Query header with expand toggle */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Text level="label-small" className="text-content-layout-1">
                      {q.name}
                    </Text>
                    <Text level="mono-small" className="text-content-layout-3">
                      {q.identifier.slice(0, 8)}
                    </Text>
                  </div>
                  <button
                    type="button"
                    onClick={() => toggleQueryExpanded(q.identifier)}
                    className="flex items-center gap-1 text-content-layout-3 hover:text-content-layout-2 transition-colors cursor-pointer"
                  >
                    <Icon
                      name={expandedQueries.has(q.identifier) ? 'chevron-down' : 'chevron-right'}
                      label="Toggle query"
                      className="w-3 h-3"
                    />
                    <Text level="caption">
                      {expandedQueries.has(q.identifier) ? 'Collapse' : 'Expand'}
                    </Text>
                  </button>
                </div>

                {/* Original SQL */}
                <div
                  className={`bg-surface-layout-1 rounded-lg p-3 overflow-auto border border-border-layout-1 ${
                    expandedQueries.has(q.identifier) ? 'max-h-80' : 'max-h-32'
                  }`}
                >
                  <SQLDisplay
                    sql={q.sql}
                    wrap
                    parameterHighlights={q.parameterHighlights}
                  />
                </div>

                {/* Parameter inputs */}
                <Show when={q.parameters.length > 0}>
                  <div className={`pl-4 border-l-2 border-border-layout-2 ${
                    q.parameters.length >= MANY_PARAMS_THRESHOLD ? 'grid grid-cols-2 gap-2' : 'space-y-2'
                  }`}>
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
