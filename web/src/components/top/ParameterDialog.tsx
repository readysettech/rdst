/**
 * Modal for entering parameter values for parameterized queries
 */

import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Modal, ModalContent, ModalContentContainer } from '@rs/ui-new/modal'
import { Text } from '@rs/ui-new/text'
import { useEffect, useMemo, useState } from 'react'
import {
  buildParameterHighlights,
  getParameterColor,
} from '../parameterHighlighting'
import { SQLDisplay } from '../SQLDisplay'
import { useFormatSql } from '../../lib/useFormatSql'
import {
  detectParameters,
  hasParameters,
  substituteParameters,
  resolveInitialValue,
} from '../../lib/sqlParameters'

export { hasParameters }

interface ParameterDialogProps {
  isOpen: boolean
  onClose: () => void
  onSubmit: (substitutedQuery: string) => void
  query: string
  initialValues?: Record<string, string | number>
  submitLabel?: string
  submitIcon?: 'speedometer' | 'play' | 'tick'
}

export function ParameterDialog({
  isOpen,
  onClose,
  onSubmit,
  query,
  initialValues,
  submitLabel = 'Analyze Query',
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

  const allFilled = Object.values(values).every((v) => v.trim() !== '')

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="extra-large" className="p-0 gap-0">
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

          {/* Content — two columns: SQL left, params right */}
          <div className="grid grid-cols-2 gap-5 p-5 h-[min(65vh,560px)]">
            {/* Left: SQL Query */}
            <div className="flex flex-col min-h-0">
              <Text
                as="label"
                level="label-small"
                className="text-content-layout-3 uppercase tracking-wider mb-2 shrink-0"
              >
                Original Query
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
              <Text
                as="label"
                level="label-small"
                className="text-content-layout-3 uppercase tracking-wider mb-2 shrink-0"
              >
                Parameters
              </Text>
              <div className="min-h-0 overflow-auto flex-1 space-y-3">
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
                <Text level="body-small" className="text-content-layout-3">
                  Strings are automatically quoted. Numbers, NULL, TRUE, FALSE
                  are passed as-is.
                </Text>
              </div>
            </div>
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
              label={submitLabel}
              icon={submitIcon}
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
