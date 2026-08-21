/**
 * Modal for entering parameter values for parameterized queries
 */

import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import * as ScrollArea from '@rs/ui-new/scroll'
import { Text } from '@rs/ui-new/text'
import { useEffect, useMemo, useRef, useState } from 'react'
import { type ParameterSuggestion, updateQueryParameters } from '../../lib/api'
import {
  buildParameterSuggestions,
  fetchParameterSchema,
  parameterValueKey,
  suggestionSummaryMessage,
} from '../../lib/parameterSuggestions'
import {
  detectParameters,
  findResidualPlaceholders,
  hasParameters,
  resolveInitialValue,
  substituteParameters,
  toBackendParams,
} from '../../lib/sqlParameters'
import { useFormatSql } from '../../lib/useFormatSql'
import { useParameterSuggestions } from '../../lib/useParameterSuggestions'
import { TaskDialogContent } from '../dialog/TaskDialogContent'
import { ParameterSuggestionSummary } from '../ParameterSuggestionSummary'
import {
  buildParameterHighlights,
  getParameterColor,
} from '../parameterHighlighting'
import { SQLDisplay } from '../SQLDisplay'

export { hasParameters }

interface ParameterDialogProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (substitutedQuery: string) => void
  query: string
  initialValues?: Record<string, unknown>
  target?: string | null
  /** pg_stat_statements queryid or performance_schema digest, when known. */
  queryHash?: string | null
  submitLabel?: string
  submitIcon?: 'speedometer' | 'play' | 'tick'
}

function sampledSuggestionKey(suggestion: ParameterSuggestion): string {
  return suggestion.placeholder === '?'
    ? `?${suggestion.index}`
    : suggestion.placeholder
}

export function ParameterDialog({
  isOpen,
  onClose,
  onSubmit,
  query,
  initialValues,
  target,
  queryHash,
  submitLabel = 'Analyze query',
  submitIcon = 'speedometer',
}: ParameterDialogProps) {
  const parameters = useMemo(() => detectParameters(query), [query])
  // Real values from the database (captured statement, sampled columns).
  const { suggestions: sampled } = useParameterSuggestions(
    isOpen ? query : null,
    target,
    queryHash,
    isOpen
  )
  const sampledByKey = useMemo(
    () =>
      new Map(
        (sampled?.placeholders ?? []).map((s) => [sampledSuggestionKey(s), s])
      ),
    [sampled]
  )
  const capturedSample = sampled?.sample ?? null
  const formattedQuery = useFormatSql(query)
  const displayQuery = formattedQuery ?? query
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
  const [provenance, setProvenance] = useState<Record<string, string>>({})
  const [suggesting, setSuggesting] = useState(false)
  const [suggestionMessage, setSuggestionMessage] = useState<string | null>(
    null
  )
  const [schemaUnavailable, setSchemaUnavailable] = useState(false)
  const [residualError, setResidualError] = useState<string | null>(null)
  const rowRefs = useRef(new Map<string, HTMLDivElement>())

  useEffect(() => {
    const init: Record<string, string> = {}
    const sources: Record<string, string> = {}
    parameters.forEach((p) => {
      const key = parameterValueKey(p)
      const value = resolveInitialValue(p, initialValues)
      init[key] = value
      if (value) sources[key] = 'Observed value'
    })
    setValues(init)
    setProvenance(sources)
    setSuggestionMessage(null)
    setSchemaUnavailable(false)
    setResidualError(null)
  }, [query, parameters, initialValues])

  const handleValueChange = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }))
    setProvenance((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setResidualError(null)
  }

  const missingCount = parameters.filter(
    (parameter) => !values[parameterValueKey(parameter)]?.trim()
  ).length

  const suggestValues = async () => {
    if (!target || missingCount === 0) return
    setSuggesting(true)
    setSuggestionMessage(null)
    setSchemaUnavailable(false)
    try {
      const schema = await fetchParameterSchema(target)
      const suggestions = buildParameterSuggestions(query, parameters, schema)
      // Values the database already knows (captured statement, sampled
      // column values) take precedence over schema-shape guesses.
      sampledByKey.forEach((suggestion, key) => {
        const best = suggestion.suggestions[0]
        if (best) {
          suggestions[key] = { value: best.value, provenance: best.provenance }
        }
      })
      const applicable = Object.entries(suggestions).filter(
        ([key]) => !values[key]?.trim()
      )
      const filled = applicable.length
      setValues((current) => {
        const next = { ...current }
        for (const [key, suggestion] of applicable) {
          if (next[key]?.trim()) continue
          next[key] = suggestion.value
        }
        return filled > 0 ? next : current
      })
      setProvenance((current) => {
        const next = { ...current }
        for (const [key, suggestion] of applicable) {
          next[key] = suggestion.provenance
        }
        return next
      })
      setSuggestionMessage(
        suggestionSummaryMessage({
          filled,
          missingBefore: missingCount,
          schemaAvailable: schema !== null,
        })
      )
      setSchemaUnavailable(schema === null)
    } catch {
      setSuggestionMessage(
        'Schema suggestions are unavailable. Existing values were preserved.'
      )
    } finally {
      setSuggesting(false)
    }
  }

  const handleSubmit = () => {
    const substituted = substituteParameters(query, parameters, values)

    // Defensive last line before running real SQL: allFilled already
    // requires every detected placeholder to carry a value, but a
    // substitution that silently failed (or a shape the detector missed)
    // must not reach the database as broken SQL. Reopen focused on the
    // first slot that did not resolve instead of running it.
    const residual = findResidualPlaceholders(substituted)
    if (residual.length > 0) {
      const token = residual[0]
      const parameter =
        parameters.find((p) => p.placeholder === token) ??
        parameters.find((p) => !values[parameterValueKey(p)]?.trim())
      const key = parameter ? parameterValueKey(parameter) : null
      setResidualError(
        key
          ? `Value for ${key} did not apply. Check the highlighted field.`
          : 'A parameter value did not apply. Re-check the values and try again.'
      )
      if (key) {
        const row = rowRefs.current.get(key)
        row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        row?.querySelector<HTMLInputElement>('input')?.focus()
      }
      return
    }
    setResidualError(null)

    if (queryHash) {
      const backendValues = toBackendParams(parameters, values)
      if (Object.keys(backendValues).length > 0) {
        void updateQueryParameters(queryHash, backendValues, 'user').catch(
          () => {
            // Persistence is a convenience for next time; it must not block
            // this run.
          }
        )
      }
    }

    onSubmit(substituted)
  }

  const allFilled = Object.values(values).every((v) => v.trim() !== '')

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <TaskDialogContent
          size="extra-large"
          icon="edit"
          title="Enter parameter values"
          description={`${parameters.length} parameter${parameters.length === 1 ? '' : 's'} detected. Values apply only to this run.`}
          bodyClassName="p-0"
          footer={
            <div className="flex items-center justify-end gap-3">
              {residualError ? (
                <Text
                  level="caption"
                  className="mr-auto text-content-negative-soft"
                >
                  {residualError}
                </Text>
              ) : null}
              <Button
                variant="primary"
                modifier="ghost"
                label="Cancel"
                onClick={onClose}
              />
              <Button
                variant="primary"
                modifier="solid"
                label={submitLabel}
                icon={submitIcon}
                iconPosition="left"
                onClick={handleSubmit}
                disabled={!allFilled}
              />
            </div>
          }
        >
          <div className="grid h-[min(65vh,560px)] grid-cols-1 gap-6 p-6 md:grid-cols-2">
            {/* Left: SQL Query */}
            <div className="flex flex-col min-h-0">
              <Text
                as="label"
                level="label-small"
                className="text-content-layout-3 uppercase tracking-wider mb-2 shrink-0"
              >
                Original query
              </Text>
              <div className="bg-surface-layout-1 rounded-lg p-3 border border-border-layout-1 min-h-0 overflow-auto flex-1 [&_.cm-scroller]:!overflow-visible">
                <SQLDisplay
                  sql={displayQuery}
                  wrap
                  parameterHighlights={parameterHighlights}
                />
              </div>
            </div>

            {/* Right: Parameters */}
            <div className="flex flex-col min-h-0">
              <div className="mb-2 flex shrink-0 items-center justify-between gap-3">
                <Text
                  as="label"
                  level="label-small"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Parameters
                </Text>
                {target && missingCount > 0 ? (
                  <Button
                    variant="primary"
                    modifier="outline"
                    size="small"
                    icon="sparkles"
                    iconPosition="left"
                    label="Suggest values"
                    loading={suggesting}
                    onClick={() => void suggestValues()}
                  />
                ) : null}
              </div>
              <ScrollArea.Root className="min-h-0 flex-1 overflow-hidden">
                <ScrollArea.Viewport className="h-full w-full custom-scrollbar">
                  <div className="space-y-3">
                    {parameters.map((param) => {
                      const key = parameterValueKey(param)
                      const colorIndex =
                        colorByPlaceholder.get(param.placeholder) ?? 0
                      const color = getParameterColor(colorIndex)
                      return (
                        <div
                          key={key}
                          ref={(node) => {
                            if (node) rowRefs.current.set(key, node)
                            else rowRefs.current.delete(key)
                          }}
                          className="flex items-center gap-3"
                        >
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
                              aria-label={`Value for ${key}`}
                              value={values[key] || ''}
                              onChange={(
                                e: React.ChangeEvent<HTMLInputElement>
                              ) => handleValueChange(key, e.target.value)}
                              placeholder="Enter value"
                            />
                            {provenance[key] ? (
                              <Text
                                level="caption"
                                className="mt-1 text-content-positive-soft"
                              >
                                {provenance[key]}
                              </Text>
                            ) : null}
                            {(() => {
                              const suggestion = sampledByKey.get(key)
                              if (
                                !suggestion ||
                                suggestion.suggestions.length === 0
                              )
                                return null
                              return (
                                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                  {suggestion.suggestions.map((s) => (
                                    <button
                                      key={`${s.provenance}:${s.value}`}
                                      type="button"
                                      title={s.provenance}
                                      onClick={() => {
                                        handleValueChange(key, s.value)
                                        setProvenance((current) => ({
                                          ...current,
                                          [key]: s.provenance,
                                        }))
                                      }}
                                      className="max-w-full truncate rounded-md border border-border-layout-2 bg-surface-layout-1 px-2 py-0.5 font-mono text-xs text-content-layout-1 hover:border-border-primary-soft hover:bg-surface-primary-soft/40"
                                    >
                                      {s.value}
                                    </button>
                                  ))}
                                  {suggestion.column ? (
                                    <Text
                                      as="span"
                                      level="caption"
                                      className="text-content-layout-3 truncate"
                                    >
                                      from {suggestion.column}
                                    </Text>
                                  ) : null}
                                </div>
                              )
                            })()}
                          </div>
                        </div>
                      )
                    })}
                    {capturedSample ? (
                      <div className="space-y-2 rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3">
                        <Text
                          level="body-small"
                          className="text-content-layout-2"
                        >
                          A real run of this query was captured (
                          {capturedSample.source}
                          {capturedSample.seen_at
                            ? `, ${capturedSample.seen_at}`
                            : ''}
                          ).
                        </Text>
                        <SQLDisplay
                          sql={capturedSample.sql}
                          wrap
                          className="max-h-32 overflow-auto rounded-md bg-surface-layout-2 p-2"
                        />
                        <Button
                          variant="primary"
                          modifier="ghost"
                          size="small"
                          label="Analyze captured statement"
                          onClick={() => onSubmit(capturedSample.sql)}
                        />
                      </div>
                    ) : null}
                    <Text level="body-small" className="text-content-layout-3">
                      Text values are quoted automatically. Numbers, NULL, TRUE,
                      and FALSE are used as entered.
                    </Text>
                    {sampledByKey.size > 0 || capturedSample ? (
                      <Text
                        level="body-small"
                        className="text-content-layout-3"
                      >
                        EXPLAIN ANALYZE runs with the values you pick, and the
                        plan can change a lot with them. Values seen in real
                        traffic (observed values, a captured run) represent
                        production best; the other suggestions are real rows
                        from the table and may produce a very different plan.
                      </Text>
                    ) : null}
                    <ParameterSuggestionSummary
                      message={suggestionMessage}
                      schemaUnavailable={schemaUnavailable}
                    />
                  </div>
                </ScrollArea.Viewport>
                <ScrollArea.Scrollbar
                  className="flex select-none touch-none bg-border-layout-2 transition-[background,width] duration-fast ease-base w-2 hover:w-4"
                  orientation="vertical"
                >
                  <ScrollArea.Thumb className="relative flex-1 bg-content-layout-disabled transition-[background] duration-fast ease-base hover:bg-content-layout-3" />
                </ScrollArea.Scrollbar>
              </ScrollArea.Root>
            </div>
          </div>
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}
