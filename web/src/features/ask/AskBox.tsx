// The question box, which is the page: a raised panel with the 17px textarea, the
// read-only caption, the Enter key and the one Ask button.
import { cn } from '@rs/tailwind-base'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { type KeyboardEvent, type RefObject, useId } from 'react'
import { DEFAULT_ASK_LIMIT } from './askModel'

export type AskEmphasis = 'solid' | 'outline' | 'ghost'

export function AskBox({
  question,
  target,
  disabled,
  submitDisabled = false,
  busy,
  emphasis,
  inputRef,
  onChange,
  onSubmit,
}: {
  question: string
  target: string | null
  disabled: boolean
  /** The question can be typed but not asked yet, as while the database resolves. */
  submitDisabled?: boolean
  /** A question is being answered: the box holds it and Ask waits. */
  busy: boolean
  /** Ask steps down while another button is the page's one solid. */
  emphasis: AskEmphasis
  inputRef?: RefObject<HTMLTextAreaElement | null>
  onChange: (question: string) => void
  onSubmit: () => void
}) {
  const id = useId()
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (!busy) onSubmit()
    }
  }
  return (
    <div
      data-testid="ask-box"
      className="rounded-card border border-border-layout-2 bg-surface-primary-soft px-4.5 pt-4 pb-3"
    >
      <BaseInputTextarea
        id={id}
        ref={inputRef}
        name="question"
        aria-label="Question"
        value={question}
        placeholder={
          target ? `Ask ${target} anything its tables can answer` : undefined
        }
        readOnly={busy}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        className="min-h-14 max-h-[10lh] resize-none rounded-none border-0 bg-transparent px-0 py-0 text-headline-5 font-normal leading-6.5 focus-visible:shadow-none disabled:opacity-100"
      />
      <div className="mt-1.5 flex items-center gap-3">
        <Text
          as="span"
          level="caption"
          className="flex-1 text-content-layout-3"
        >
          {target
            ? `Read-only on ${target}. ${DEFAULT_ASK_LIMIT} rows unless you ask for more.`
            : 'Choose a database to ask.'}
        </Text>
        <kbd className="rounded-sm border border-border-layout-2 px-1.5 font-mono text-label-extra-small font-normal text-content-layout-3">
          Enter
        </kbd>
        <Button
          variant="primary"
          modifier={emphasis}
          label="Ask"
          disabled={disabled || submitDisabled || busy || !question.trim()}
          className={cn(busy && 'opacity-50')}
          onClick={onSubmit}
        />
      </div>
    </div>
  )
}
