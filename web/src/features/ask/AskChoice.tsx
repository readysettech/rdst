// An ambiguous question: every clarifying question on one screen, then one Run. Each
// question lists its readings as rows with a caption where it says more than the label
// (the schema identifier the option reads, when Ask has one), and nothing is picked for the
// user. A question that allows several readings takes any of them. A question without
// options asks for a value or definition: Ask's suggested answers are rows, or there is
// only a text box. "None of these" takes the answer in the user's own words. A question
// left blank is skipped and Ask goes with its best guess, as the CLI skips one on Enter.
import { cn } from '@rs/tailwind-base'
import { BaseInputCheckboxRow } from '@rs/ui-new/base-input-checkbox'
import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { type ComponentProps, useEffect, useRef, useState } from 'react'
import type { AskClarificationNeededEvent } from '@/lib/ask'
import { useAutoGrow } from './useAutoGrow'

// The value of "None of these"; option values are the options' own text.
const NONE_OF_THESE = '__none_of_these__'
const NONE_ROW = {
  value: NONE_OF_THESE,
  label: 'None of these',
  description: 'Describe it in your own words',
}

// A question as Ask asked it, or as an answer reports what it offered.
export type AskedQuestion = {
  id: string
  question: string
  options?: string[]
  hints?: string[]
  examples?: string[]
  multiple?: boolean
}
type Row = { value: string; label: string; description?: string }
const HINT_EXAMPLE = ', e.g. '

export function AskChoice({
  clarification,
  disabled,
  onRun,
}: {
  clarification: AskClarificationNeededEvent
  disabled: boolean
  onRun: (answers: Record<string, string>) => void
}) {
  // The picked rows of each question, and what the user typed for it.
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [typed, setTyped] = useState<Record<string, string>>({})
  const questions = clarification.questions
  // Interpretations pair with options by position only when there is a single question.
  const single = questions.length === 1
  const answerOf = (question: AskedQuestion) =>
    answerFrom(picked[question.id] ?? [], typed[question.id] ?? '')
  const answers = Object.fromEntries(
    questions
      .map((question) => [question.id, answerOf(question)] as const)
      .filter(([, answer]) => answer)
  )
  const blank = questions.length - Object.keys(answers).length
  return (
    <div data-testid="ask-choice" className="mt-5 flex flex-col gap-6">
      {questions.map((question, index) => (
        <AskQuestion
          key={question.id}
          question={question}
          number={
            questions.length > 1 ? `${index + 1}/${questions.length}` : null
          }
          rows={rowsFor(
            question,
            single ? clarification.interpretations : undefined
          )}
          picked={picked[question.id] ?? []}
          typed={typed[question.id] ?? ''}
          disabled={disabled}
          onPick={(rows) =>
            setPicked((current) => ({ ...current, [question.id]: rows }))
          }
          onType={(text) =>
            setTyped((current) => ({ ...current, [question.id]: text }))
          }
        />
      ))}
      <div className="flex items-center justify-end gap-3">
        {blank > 0 && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {blank === questions.length
              ? 'Ask uses its best guess for each question.'
              : `Ask uses its best guess for the ${blank === 1 ? 'blank question' : `${blank} blank questions`}.`}
          </Text>
        )}
        <Button
          variant="primary"
          modifier="solid"
          size="small"
          label="Run"
          disabled={disabled}
          onClick={() => onRun(answers)}
        />
      </div>
    </div>
  )
}

// The answer the picked rows give, or the typed one when none fits.
export function answerFrom(picked: string[], typed: string): string {
  if (picked.length === 0 || picked.includes(NONE_OF_THESE)) return typed.trim()
  return picked.join('; ')
}

// The rows and typed text that show an answer already given, for changing it.
export function pickedFor(
  question: AskedQuestion,
  answer: string
): { picked: string[]; typed: string } {
  if (!answer) return { picked: [], typed: '' }
  const rows = rowsFor(question).map((row) => row.value)
  const parts = question.multiple
    ? answer.split(';').map((part) => part.trim())
    : [answer]
  if (rows.length > 0 && parts.every((part) => rows.includes(part)))
    return { picked: parts, typed: '' }
  return { picked: rows.length > 0 ? [NONE_OF_THESE] : [], typed: answer }
}

// A hint names the column an option reads and may add one of its values. The page shows
// the value alone: it tells readings apart in the user's own terms.
function exampleIn(hint?: string) {
  const example = hint?.split(HINT_EXAMPLE)[1]?.trim()
  return example ? `e.g. ${example}` : undefined
}

export function rowsFor(
  question: AskedQuestion,
  interpretations?: AskClarificationNeededEvent['interpretations']
): Row[] {
  if (!question.options?.length)
    return (question.examples ?? []).map((example) => ({
      value: example,
      label: example,
    }))
  return question.options.map((option, position) => {
    const caption =
      exampleIn(question.hints?.[position]) ||
      interpretations?.[position]?.description?.trim()
    return {
      value: option,
      label: option,
      description:
        caption && caption.toLowerCase() !== option.trim().toLowerCase()
          ? caption
          : undefined,
    }
  })
}

export function AskQuestion({
  question,
  number,
  rows,
  picked,
  typed,
  disabled,
  onPick,
  onType,
  quiet = false,
}: {
  question: AskedQuestion
  number: string | null
  rows: Row[]
  picked: string[]
  typed: string
  disabled: boolean
  onPick: (rows: string[]) => void
  onType: (text: string) => void
  /** The question already shows above, so it is named for screen readers only. */
  quiet?: boolean
}) {
  const several = Boolean(question.multiple) && rows.length > 0
  const describing = picked.includes(NONE_OF_THESE)
  // "None of these" starts from the reading picked before it, so a reading that is nearly
  // right takes a small edit; the text is selected, so typing replaces it.
  const describe = () => {
    if (!typed.trim()) onType(answerFrom(picked, ''))
    onPick([NONE_OF_THESE])
  }
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="p-0 text-body-small text-content-layout-2">
        {number && <span className="mr-2 text-content-layout-3">{number}</span>}
        <span className={cn(quiet && 'sr-only')}>{question.question}</span>
        {several && (
          <span className="text-content-layout-3"> Pick any that apply.</span>
        )}
      </legend>
      {rows.length === 0 ? (
        <AnswerBox
          aria-label={question.question}
          value={typed}
          disabled={disabled}
          placeholder="Your answer"
          onChange={(event) => onType(event.target.value)}
        />
      ) : several ? (
        <div className="mt-1.5 grid overflow-hidden rounded-lg border-(length:--border-base) border-border-layout-1 bg-surface-layout-1">
          {[...rows, NONE_ROW].map((row) => {
            const checked = picked.includes(row.value)
            return (
              <BaseInputCheckboxRow
                key={row.value}
                checked={checked}
                disabled={disabled}
                aria-label={row.label}
                onCheckedChange={(on) => {
                  // "None of these" stands alone: it clears the readings, and a reading
                  // clears it.
                  if (row.value === NONE_OF_THESE) {
                    if (on) describe()
                    else onPick([])
                  } else
                    onPick(
                      on
                        ? [
                            ...picked.filter((v) => v !== NONE_OF_THESE),
                            row.value,
                          ]
                        : picked.filter((v) => v !== row.value)
                    )
                }}
                className={cn(
                  'min-h-10 items-start border-(length:--border-base) border-transparent px-3 py-2 text-content-layout-3',
                  checked &&
                    'border-border-primary-solid bg-surface-primary-soft text-content-primary-soft'
                )}
              >
                <span className="flex flex-col items-start gap-1">
                  <Text level="label-medium" className="text-left">
                    {row.label}
                  </Text>
                  {row.description && (
                    <Text
                      level="caption"
                      className="text-left text-content-layout-3"
                    >
                      {row.description}
                    </Text>
                  )}
                </span>
              </BaseInputCheckboxRow>
            )
          })}
        </div>
      ) : (
        <BaseInputRadioGroup
          aria-label={question.question}
          className="mt-1.5"
          options={[...rows, NONE_ROW]}
          value={picked[0] ?? ''}
          disabled={disabled}
          onValueChange={(value) =>
            value === NONE_OF_THESE ? describe() : onPick([value])
          }
        />
      )}
      {describing && (
        <AnswerBox
          aria-label="Describe what you mean"
          selectOnShow
          value={typed}
          disabled={disabled}
          placeholder="Type your own answer"
          onChange={(event) => onType(event.target.value)}
        />
      )}
    </fieldset>
  )
}

// A one-line text box that grows with the answer, up to the textarea's own maximum.
// `selectOnShow` focuses it with its text selected when it appears.
function AnswerBox({
  selectOnShow = false,
  ...props
}: ComponentProps<typeof BaseInputTextarea> & { selectOnShow?: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useAutoGrow(ref, props.value)
  useEffect(() => {
    if (!selectOnShow) return
    ref.current?.focus()
    ref.current?.select()
  }, [selectOnShow])
  return (
    <BaseInputTextarea
      ref={ref}
      rows={1}
      className="mt-1.5 min-h-0 resize-none"
      {...props}
    />
  )
}
