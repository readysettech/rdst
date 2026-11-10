// An ambiguous question: the clarifying question, the interpretations as radio rows with a
// caption where it says more than the label, and one solid "Run with <choice>", inline
// under the box. A question without options asks for a value or definition in a text box;
// left blank it is skipped, as the CLI skips one on Enter.
import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { AskClarificationNeededEvent } from '@/lib/ask'

export function AskChoice({
  clarification,
  disabled,
  onRun,
}: {
  clarification: AskClarificationNeededEvent
  disabled: boolean
  onRun: (answers: Record<string, string>) => void
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [index, setIndex] = useState(0)
  const question = clarification.questions[index]
  if (!question) return null
  // Interpretations pair with options by position only when there is a single question.
  const single = clarification.questions.length === 1
  const options = question.options.map((option, position) => {
    const caption = single
      ? clarification.interpretations[position]?.description?.trim()
      : undefined
    return {
      value: option,
      label: option,
      description:
        caption && caption.toLowerCase() !== option.trim().toLowerCase()
          ? caption
          : undefined,
    }
  })
  const open = question.options.length === 0
  const chosen = answers[question.id] ?? question.options[0] ?? ''
  const last = index === clarification.questions.length - 1
  return (
    <div data-testid="ask-choice" className="mt-5">
      <Text as="p" level="body-small" className="text-content-layout-2">
        {question.question}
      </Text>
      {open ? (
        <BaseInputText
          aria-label={question.question}
          className="mt-1.5"
          value={chosen}
          disabled={disabled}
          autoComplete="off"
          placeholder="Your answer"
          onChange={(event) => {
            const value = event.target.value
            setAnswers((current) => ({ ...current, [question.id]: value }))
          }}
        />
      ) : (
        <BaseInputRadioGroup
          aria-label={question.question}
          className="mt-1.5"
          options={options}
          value={chosen}
          disabled={disabled}
          onValueChange={(value) =>
            setAnswers((current) => ({ ...current, [question.id]: value }))
          }
        />
      )}
      <div className="mt-3.5 flex justify-end">
        <Button
          variant="primary"
          modifier="solid"
          size="small"
          label={
            open
              ? chosen.trim()
                ? 'Run'
                : 'Skip'
              : `Run with ${chosen.toLowerCase()}`
          }
          disabled={disabled || (!open && !chosen.trim())}
          onClick={() => {
            const { [question.id]: _, ...rest } = answers
            const next = chosen.trim()
              ? { ...rest, [question.id]: chosen.trim() }
              : rest
            if (last) onRun(next)
            else {
              setAnswers(next)
              setIndex(index + 1)
            }
          }}
        />
      </div>
    </div>
  )
}
