// What Ask assumed to answer first: each point it settled, as the question it stands for
// and the answer it went with, and a Change that opens the same choices Ask would have
// asked with. Any number of points can be changed before running again; the question is
// answered with the changed points and keeps the rest.
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { AskClarification } from '@/lib/ask'
import { AskQuestion, answerFrom, pickedFor, rowsFor } from './AskChoice'

type Edit = { picked: string[]; typed: string }

export function AskAssumptions({
  clarifications,
  disabled,
  onChange,
}: {
  clarifications: AskClarification[]
  disabled: boolean
  onChange: (clarifications: AskClarification[]) => void
}) {
  // The points being changed, by question ID.
  const [edits, setEdits] = useState<Record<string, Edit>>({})
  if (clarifications.length === 0) return null
  const changed = clarifications.flatMap((item) => {
    const edit = edits[item.id]
    const answer = edit ? answerFrom(edit.picked, edit.typed) : ''
    return answer && answer !== item.answer ? [[item.id, answer] as const] : []
  })
  const answers = Object.fromEntries(changed)
  const editing = Object.keys(edits).length > 0
  const edit = (id: string, next: Partial<Edit>) =>
    setEdits((current) => ({
      ...current,
      [id]: { ...(current[id] ?? { picked: [], typed: '' }), ...next },
    }))
  return (
    <section
      aria-label="What Ask assumed"
      data-testid="ask-assumptions"
      className="mt-4 rounded-lg border border-border-layout-2 px-4 py-3"
    >
      <Text as="p" level="label-small" className="text-content-layout-2">
        {clarifications.some((item) => item.assumed)
          ? 'Ask assumed'
          : 'Your answers'}
      </Text>
      <ul className="m-0 mt-1 flex list-none flex-col p-0">
        {clarifications.map((item) => (
          <li
            key={item.id}
            className="border-t border-border-layout-2 py-2 first:border-t-0"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Text as="p" level="caption" className="text-content-layout-3">
                  {item.question}
                </Text>
                <Text
                  as="p"
                  level="body-small"
                  className="text-content-layout-1"
                >
                  {item.answer || 'Ask worked it out from the data'}
                  {!item.assumed && (
                    <span className="text-content-layout-3">
                      {' '}
                      (your answer)
                    </span>
                  )}
                </Text>
              </div>
              {!edits[item.id] && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Change"
                  aria-label={`Change: ${item.question}`}
                  disabled={disabled}
                  onClick={() => edit(item.id, pickedFor(item, item.answer))}
                />
              )}
            </div>
            {edits[item.id] && (
              <div className="mt-2">
                <AskQuestion
                  question={item}
                  number={null}
                  rows={rowsFor(item)}
                  picked={edits[item.id].picked}
                  typed={edits[item.id].typed}
                  disabled={disabled}
                  onPick={(picked) => edit(item.id, { picked })}
                  onType={(typed) => edit(item.id, { typed })}
                  quiet
                />
              </div>
            )}
          </li>
        ))}
      </ul>
      {editing && (
        <div className="mt-2 flex items-center justify-end gap-1.5">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Cancel"
            onClick={() => setEdits({})}
          />
          <Button
            variant="primary"
            modifier="solid"
            size="small"
            label={
              changed.length > 1
                ? `Run again with ${changed.length} changes`
                : 'Run again'
            }
            disabled={disabled || changed.length === 0}
            onClick={() => {
              setEdits({})
              onChange(
                clarifications.map((item) =>
                  item.id in answers
                    ? { ...item, answer: answers[item.id], assumed: false }
                    : item
                )
              )
            }}
          />
        </div>
      )}
    </section>
  )
}
