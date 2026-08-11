import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useCallback, useState } from 'react'
import type { AskClarificationQuestion } from '../../lib/ask'

interface AskClarificationProps {
  question?: string
  questions: AskClarificationQuestion[]
  onSubmit: (answers: Record<string, string>) => void
  onEditQuestion?: () => void
  disabled?: boolean
}

const CUSTOM_OPTION_VALUE = '__custom__'

function buildAnswers(
  selectedOptions: Record<string, string>,
  customInputs: Record<string, string>
): Record<string, string> {
  const answers: Record<string, string> = {}
  for (const [id, value] of Object.entries(selectedOptions)) {
    if (value === CUSTOM_OPTION_VALUE) {
      const custom = customInputs[id]?.trim()
      if (custom) answers[id] = custom
    } else {
      answers[id] = value
    }
  }
  return answers
}

function ClarificationOption({
  label,
  selected,
  onSelect,
  disabled,
}: {
  label: string
  selected: boolean
  onSelect: () => void
  disabled?: boolean
}) {
  return (
    <Pressable
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      disabled={disabled}
      className={`flex w-full items-center gap-3 rounded-xl border-(length:--border-base) px-4 py-3 text-left transition-colors duration-fast ease-base focus-visible:outline-none focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? 'border-border-primary-solid bg-surface-primary-soft'
          : 'border-border-layout-1 bg-surface-layout-1 hover:border-border-primary-soft'
      }`}
    >
      <span
        className={`flex size-4 shrink-0 items-center justify-center rounded-full border-(length:--border-base) ${
          selected ? 'border-border-primary-solid' : 'border-border-layout-2'
        }`}
      >
        {selected && (
          <span className="size-2 rounded-full bg-content-primary-soft" />
        )}
      </span>
      <Text
        level="label-medium"
        className={
          selected ? 'text-content-primary-soft' : 'text-content-layout-2'
        }
      >
        {label}
      </Text>
    </Pressable>
  )
}

export function AskClarification({
  question,
  questions,
  onSubmit,
  onEditQuestion,
  disabled = false,
}: AskClarificationProps) {
  const [selectedOptions, setSelectedOptions] = useState<
    Record<string, string>
  >({})
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({})
  const [currentIndex, setCurrentIndex] = useState(0)

  const currentQuestion = questions[currentIndex]
  const isLastQuestion = currentIndex === questions.length - 1
  const selectedValue = currentQuestion
    ? selectedOptions[currentQuestion.id]
    : undefined
  const customValue = currentQuestion ? customInputs[currentQuestion.id] : ''
  const hasAnswer =
    currentQuestion &&
    ((selectedValue === CUSTOM_OPTION_VALUE && customValue.trim().length > 0) ||
      (selectedValue && selectedValue !== CUSTOM_OPTION_VALUE))

  const handleSelect = useCallback((questionId: string, option: string) => {
    setSelectedOptions((previous) => ({
      ...previous,
      [questionId]: option,
    }))
  }, [])

  const handleCustomChange = useCallback(
    (questionId: string, value: string) => {
      setCustomInputs((previous) => ({
        ...previous,
        [questionId]: value,
      }))
    },
    []
  )

  const clearQuestion = useCallback((questionId: string) => {
    setSelectedOptions((previous) => {
      const next = { ...previous }
      delete next[questionId]
      return next
    })
    setCustomInputs((previous) => {
      const next = { ...previous }
      delete next[questionId]
      return next
    })
  }, [])

  const handleNext = useCallback(() => {
    const activeQuestion = questions[currentIndex]
    if (!activeQuestion) return

    if (selectedOptions[activeQuestion.id] === CUSTOM_OPTION_VALUE) {
      const value = customInputs[activeQuestion.id]?.trim()
      if (!value) return
    }

    const answers = buildAnswers(selectedOptions, customInputs)
    if (isLastQuestion) {
      onSubmit(answers)
    } else {
      setCurrentIndex((previous) => previous + 1)
    }
  }, [
    questions,
    currentIndex,
    selectedOptions,
    customInputs,
    isLastQuestion,
    onSubmit,
  ])

  const handleSkip = useCallback(() => {
    if (disabled) return
    const activeQuestion = questions[currentIndex]
    if (!activeQuestion) return

    clearQuestion(activeQuestion.id)
    if (isLastQuestion) {
      const answers = buildAnswers(selectedOptions, customInputs)
      delete answers[activeQuestion.id]
      onSubmit(answers)
    } else {
      setCurrentIndex((previous) => previous + 1)
    }
  }, [
    disabled,
    questions,
    currentIndex,
    clearQuestion,
    isLastQuestion,
    selectedOptions,
    customInputs,
    onSubmit,
  ])

  if (!currentQuestion) return null

  const questionText = currentQuestion.question.split(':')[0].trim()

  return (
    <VStack className="gap-5 items-start w-full">
      <VStack className="gap-1 items-start w-full">
        <Text level="headline-5" className="text-content-layout-1">
          One quick question
        </Text>
        {question && (
          <Text level="body-small" className="text-content-layout-3">
            You asked: <span className="text-content-layout-2">{question}</span>
          </Text>
        )}
      </VStack>

      <Card className="w-full">
        <Card.Header className="border-b border-border-layout-1">
          <HStack className="justify-between items-center w-full">
            <HStack className="gap-3 items-center">
              <div className="flex size-8 items-center justify-center rounded-lg bg-surface-primary-soft">
                <Text
                  level="label-medium"
                  className="text-content-primary-soft"
                >
                  {currentIndex + 1}
                </Text>
              </div>
              <Text
                level="overline"
                className="uppercase tracking-wider text-content-layout-3"
              >
                Question {currentIndex + 1} of {questions.length}
              </Text>
            </HStack>
            <HStack className="gap-1.5">
              {questions.map((item, index) => (
                <m.div
                  key={item.id}
                  className={`size-2 rounded-full transition-colors ${
                    index < currentIndex
                      ? 'bg-content-positive-soft'
                      : index === currentIndex
                        ? 'bg-content-primary-soft'
                        : 'bg-surface-layout-2'
                  }`}
                  initial={false}
                  animate={{ scale: index === currentIndex ? 1.2 : 1 }}
                />
              ))}
            </HStack>
          </HStack>
        </Card.Header>
        <Card.Content className="p-5">
          <VStack className="gap-5 items-start w-full">
            <Text level="headline-5" className="text-content-layout-1">
              {questionText}
            </Text>
            <VStack className="gap-3 w-full">
              <div
                role="radiogroup"
                aria-label={questionText}
                className="grid w-full gap-2"
              >
                {currentQuestion.options.map((option) => (
                  <ClarificationOption
                    key={option}
                    label={option}
                    selected={selectedValue === option}
                    onSelect={() => handleSelect(currentQuestion.id, option)}
                    disabled={disabled}
                  />
                ))}
                <ClarificationOption
                  label="Something else (let me type it)"
                  selected={selectedValue === CUSTOM_OPTION_VALUE}
                  onSelect={() =>
                    handleSelect(currentQuestion.id, CUSTOM_OPTION_VALUE)
                  }
                  disabled={disabled}
                />
              </div>
              <Show when={selectedValue === CUSTOM_OPTION_VALUE}>
                <BaseInputText
                  name={`custom-${currentQuestion.id}`}
                  placeholder="Type your own answer..."
                  value={customValue || ''}
                  onChange={(event) =>
                    handleCustomChange(currentQuestion.id, event.target.value)
                  }
                  disabled={disabled}
                />
              </Show>
            </VStack>
          </VStack>
        </Card.Content>
        <Card.Footer className="border-t border-border-layout-1">
          <HStack className="justify-between items-center w-full">
            {onEditQuestion ? (
              <Button
                onClick={onEditQuestion}
                variant="primary"
                modifier="ghost"
                label="Edit question"
                icon="arrow-left"
                iconPosition="left"
                disabled={disabled}
              />
            ) : (
              <span />
            )}
            <HStack className="gap-2 items-center">
              <Button
                onClick={handleSkip}
                variant="primary"
                modifier="ghost"
                label="Skip"
                icon="arrow-right"
                iconPosition="right"
                disabled={disabled}
              />
              <Button
                onClick={handleNext}
                disabled={disabled || !hasAnswer}
                variant="rising"
                modifier="solid"
                label={isLastQuestion ? 'Get answer' : 'Next'}
                icon={isLastQuestion ? 'sparkles' : 'arrow-right'}
                iconPosition="right"
              />
            </HStack>
          </HStack>
        </Card.Footer>
      </Card>
    </VStack>
  )
}
