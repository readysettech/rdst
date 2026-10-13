/**
 * Parameter-value entry for a parameterized query, as a blocking modal or as
 * an inline panel (`presentation`).
 */

import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { ModalDescription } from '@rs/ui-new/modal'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
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
import { DialogShell } from '../DialogShell'
import { ParameterSuggestionSummary } from '../ParameterSuggestionSummary'
import { buildParameterHighlights } from '../parameterHighlighting'
import { SQLDisplay } from '../SQLDisplay'

export { hasParameters }

interface ParameterDialogProps {
  isOpen: boolean
  onClose: () => void
  /**
   * Escape over an inline form, when stepping out of the form is not the same
   * as leaving the surface around it. Defaults to `onClose`.
   */
  onEscape?: () => void
  onSubmit: (substitutedQuery: string) => void
  query: string
  initialValues?: Record<string, unknown>
  target?: string | null
  /** pg_stat_statements queryid or performance_schema digest, when known. */
  queryHash?: string | null
  submitLabel?: string
  /**
   * `'modal'` blocks the page it belongs to. `'inline'` renders the same form
   * as a panel in the surrounding flow, for surfaces that are already an
   * overlay — a dialog stacked on the analyze drawer would cover it and, as a
   * higher dismissable layer, leave everything under it unclickable.
   */
  presentation?: 'modal' | 'inline'
}

function sampledSuggestionKey(suggestion: ParameterSuggestion): string {
  return suggestion.placeholder === '?'
    ? `?${suggestion.index}`
    : suggestion.placeholder
}

export function ParameterDialog({
  isOpen,
  onClose,
  onEscape,
  onSubmit,
  query,
  initialValues,
  target,
  queryHash,
  submitLabel = 'Analyze query',
  presentation = 'modal',
}: ParameterDialogProps) {
  const isInline = presentation === 'inline'
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
  const [values, setValues] = useState<Record<string, string>>({})
  const [provenance, setProvenance] = useState<Record<string, string>>({})
  const [suggesting, setSuggesting] = useState(false)
  const [suggestionMessage, setSuggestionMessage] = useState<string | null>(
    null
  )
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
  const description = `${parameters.length} parameter${parameters.length === 1 ? '' : 's'} detected. Values apply only to this run.`

  // Escape belongs to the innermost open layer. The inline form is a layer the
  // overlay it sits in knows nothing about, so it claims the key first — from
  // `window`, which the capture phase reaches before the document the overlay
  // listens on — and steps back out of the form alone, keeping the typed
  // values' surroundings in place. [B-17]
  useEffect(() => {
    if (!isInline || !isOpen) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      event.preventDefault()
      ;(onEscape ?? onClose)()
    }
    window.addEventListener('keydown', handleEscape, { capture: true })
    return () =>
      window.removeEventListener('keydown', handleEscape, { capture: true })
  }, [isInline, isOpen, onClose, onEscape])

  const actions = (
    <>
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
        onClick={handleSubmit}
        disabled={!allFilled}
      />
    </>
  )
  const residualNote = residualError ? (
    <span className="text-content-negative-soft">{residualError}</span>
  ) : null

  const body = (
    <div className="space-y-4">
      {isInline ? (
        <Text as="p" level="body-small" className="text-content-layout-2">
          {description}
        </Text>
      ) : (
        <ModalDescription>{description}</ModalDescription>
      )}
      <div>
        <Text as="p" level="caption" className="mb-1.5 text-content-layout-3">
          Query
        </Text>
        <Scrollable
          rootClassName="rounded-control bg-surface-layout-2"
          className="max-h-48 px-3.5 py-3 [&_.cm-scroller]:!overflow-visible"
        >
          <SQLDisplay
            sql={displayQuery}
            wrap
            parameterHighlights={parameterHighlights}
          />
        </Scrollable>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between gap-3">
          <Text as="p" level="caption" className="text-content-layout-3">
            Parameters
          </Text>
          {target && missingCount > 0 ? (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Suggest values"
              loading={suggesting}
              onClick={() => void suggestValues()}
            />
          ) : null}
        </div>
        <div className="space-y-3">
          {parameters.map((param) => {
            const key = parameterValueKey(param)
            const suggestion = sampledByKey.get(key)
            return (
              <div
                key={key}
                ref={(node) => {
                  if (node) rowRefs.current.set(key, node)
                  else rowRefs.current.delete(key)
                }}
                className="flex items-start gap-3"
              >
                <Text
                  as="span"
                  level="mono-small"
                  className="w-14 shrink-0 pt-2 text-right text-content-layout-2"
                >
                  {key}
                </Text>
                <div className="min-w-0 flex-1">
                  <BaseInputText
                    name={`param-${key}`}
                    aria-label={`Value for ${key}`}
                    value={values[key] || ''}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      handleValueChange(key, e.target.value)
                    }
                    placeholder="Enter value"
                  />
                  {provenance[key] ? (
                    <Text
                      level="caption"
                      className="mt-1 text-content-layout-3"
                    >
                      {provenance[key]}
                    </Text>
                  ) : null}
                  {suggestion && suggestion.suggestions.length > 0 ? (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {suggestion.suggestions.map((s) => (
                        <Pressable
                          key={`${s.provenance}:${s.value}`}
                          title={s.provenance}
                          onClick={() => {
                            handleValueChange(key, s.value)
                            setProvenance((current) => ({
                              ...current,
                              [key]: s.provenance,
                            }))
                          }}
                          className="max-w-full truncate rounded-control bg-surface-layout-2 px-2 py-0.5 font-mono text-mono-small text-content-layout-1 hover:bg-surface-primary-soft"
                        >
                          {s.value}
                        </Pressable>
                      ))}
                      {suggestion.column ? (
                        <Text
                          as="span"
                          level="caption"
                          className="truncate text-content-layout-3"
                        >
                          from {suggestion.column}
                        </Text>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      </div>
      {capturedSample ? (
        <div className="space-y-2">
          <Text as="p" level="body-small" className="text-content-layout-2">
            A real run of this query was captured ({capturedSample.source}
            {capturedSample.seen_at ? `, ${capturedSample.seen_at}` : ''}).
          </Text>
          <Scrollable
            rootClassName="rounded-control bg-surface-layout-2"
            className="max-h-32 px-3.5 py-3"
          >
            <SQLDisplay sql={capturedSample.sql} wrap />
          </Scrollable>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Analyze captured statement"
            onClick={() => onSubmit(capturedSample.sql)}
          />
        </div>
      ) : null}
      <Text as="p" level="caption" className="text-content-layout-3">
        Text values are quoted automatically. Numbers, NULL, TRUE, and FALSE are
        used as entered.
      </Text>
      {sampledByKey.size > 0 || capturedSample ? (
        <Text as="p" level="caption" className="text-content-layout-3">
          EXPLAIN ANALYZE runs with the values you pick, and the plan can change
          a lot with them. Values seen in real traffic (observed values, a
          captured run) represent production best; the other suggestions are
          real rows from the table and may produce a very different plan.
        </Text>
      ) : null}
      <ParameterSuggestionSummary message={suggestionMessage} />
    </div>
  )

  if (isInline) {
    if (!isOpen) return null
    return (
      <section
        aria-label="Enter parameter values"
        data-testid="parameter-form-inline"
        // A value field is a single-line form field, so Enter runs the form
        // the way Enter runs every other one. [B-17]
        onKeyDown={(event) => {
          if (
            event.key === 'Enter' &&
            allFilled &&
            event.target instanceof HTMLInputElement
          ) {
            event.preventDefault()
            handleSubmit()
          }
        }}
      >
        {body}
        <div className="mt-5 flex items-center justify-end gap-3">
          {residualNote ? (
            <Text as="p" level="caption" className="mr-auto">
              {residualNote}
            </Text>
          ) : null}
          {actions}
        </div>
      </section>
    )
  }

  return (
    <DialogShell
      open={isOpen}
      onClose={onClose}
      title="Enter parameter values"
      note={residualNote}
      action={actions}
    >
      {body}
    </DialogShell>
  )
}
