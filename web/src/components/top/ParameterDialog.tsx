/**
 * Modal for entering parameter values for parameterized queries
 */

import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import * as ScrollArea from '@rs/ui-new/scroll'
import { Text } from '@rs/ui-new/text'
import { useEffect, useMemo, useState } from 'react'
import {
  buildParameterSuggestions,
  fetchParameterSchema,
  parameterValueKey,
} from '../../lib/parameterSuggestions'
import {
  detectParameters,
  hasParameters,
  resolveInitialValue,
  substituteParameters,
} from '../../lib/sqlParameters'
import { useFormatSql } from '../../lib/useFormatSql'
import { TaskDialogContent } from '../dialog/TaskDialogContent'
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
  submitLabel?: string
  submitIcon?: 'speedometer' | 'play' | 'tick'
}

export function ParameterDialog({
  isOpen,
  onClose,
  onSubmit,
  query,
  initialValues,
  target,
  submitLabel = 'Analyze query',
  submitIcon = 'speedometer',
}: ParameterDialogProps) {
  const parameters = useMemo(() => detectParameters(query), [query])
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
  }, [query, parameters, initialValues])

  const handleValueChange = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }))
    setProvenance((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
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
        filled > 0
          ? `Filled ${filled} unresolved ${filled === 1 ? 'parameter' : 'parameters'} from safe schema evidence. Review before running.`
          : 'No safe schema-grounded suggestions were found. Existing values were preserved.'
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
            <div className="flex justify-end gap-3">
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
                          </div>
                        </div>
                      )
                    })}
                    <Text level="body-small" className="text-content-layout-3">
                      Text values are quoted automatically. Numbers, NULL, TRUE,
                      and FALSE are used as entered.
                    </Text>
                    {suggestionMessage ? (
                      <Text level="caption" className="text-content-layout-2">
                        {suggestionMessage}
                      </Text>
                    ) : null}
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
