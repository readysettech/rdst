import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  type KeyboardEvent,
  type ReactNode,
  useLayoutEffect,
  useRef,
} from 'react'

/**
 * The keyboard contract both composers honour, printed where it applies so a
 * user who presses Enter mid-sentence is not surprised by it (C-05).
 */
export const COMPOSER_KEY_HINT = 'Enter to ask · Shift+Enter for a new line'

export interface PromptComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  /** Accessible name for the textarea — a placeholder is not one (C-04). */
  label: string
  placeholder: string
  /** The composer cannot be used at all (no target, no key). */
  disabled?: boolean
  /** A request is in flight: the action reports it, the field waits for it. */
  busy?: boolean
  submitLabel: string
  submitIcon?: IconStrokeName
  submitIconPosition?: 'left' | 'right'
  submitVariant?: 'primary' | 'rising'
  submitSize?: 'small' | 'base'
  /**
   * The field's height range, as the pair of line-height classes the call site
   * wants: a chat prompt starts at two lines, a page's question box at seven.
   */
  fieldClassName: string
  /** Grow the field with its content, up to the `fieldClassName` ceiling. */
  autoGrow?: boolean
  /** Printed under the field; pass `null` where the surface says it already. */
  hint?: ReactNode
  /** Sits beside the hint, left of the action (e.g. the read-only chip). */
  accessory?: ReactNode
}

/**
 * One prompt composer: a field that grows with what is typed, and one action
 * sized to its label beside it. `/ask` and the analysis follow-up share it, so
 * the send action stays a send action on both instead of becoming a full-width
 * slab on one of them (C-01, C-02, Mike #9).
 */
export function PromptComposer({
  value,
  onChange,
  onSubmit,
  label,
  placeholder,
  disabled,
  busy,
  submitLabel,
  submitIcon,
  submitIconPosition = 'left',
  submitVariant = 'primary',
  submitSize = 'base',
  fieldClassName,
  autoGrow,
  hint = COMPOSER_KEY_HINT,
  accessory,
}: PromptComposerProps) {
  const fieldRef = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const field = fieldRef.current
    if (!field || !autoGrow) return
    // Measure against a collapsed field so the height follows the text down
    // as well as up; the max-height class keeps the ceiling.
    field.style.height = 'auto'
    if (field.scrollHeight > 0) field.style.height = `${field.scrollHeight}px`
  }, [autoGrow, value])

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    onSubmit()
  }

  return (
    <VStack className="items-stretch gap-2">
      <BaseInputTextarea
        ref={fieldRef}
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled || busy}
        className={`w-full text-body-medium ${fieldClassName}${autoGrow ? ' resize-none overflow-y-auto' : ''}`}
      />
      <HStack className="flex-wrap items-center justify-between gap-3">
        <HStack className="min-w-0 flex-wrap items-center gap-3">
          {accessory}
          {hint ? (
            <Text level="caption" className="text-content-layout-3">
              {hint}
            </Text>
          ) : null}
        </HStack>
        <Button
          // Stays at the right edge even when the row wraps at narrow widths.
          className="ml-auto"
          onClick={onSubmit}
          disabled={disabled || busy || !value.trim()}
          loading={busy}
          variant={submitVariant}
          modifier="solid"
          size={submitSize}
          label={submitLabel}
          icon={submitIcon}
          iconPosition={submitIcon ? submitIconPosition : 'none'}
        />
      </HStack>
    </VStack>
  )
}
