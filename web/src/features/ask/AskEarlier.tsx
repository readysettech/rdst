// Earlier: one divided list of the target's questions grouped by day, each with its
// time and a ghost "Ask again". A search field appears only past twenty questions.
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { Fragment, useMemo, useState } from 'react'
import type { AskHistoryItem } from '@/lib/api'
import { groupByDay, whenWord } from './askModel'

const SEARCH_FROM = 20

export function AskEarlier({
  items,
  target,
  loading,
  disabled,
  nowMs,
  onAskAgain,
}: {
  items: AskHistoryItem[]
  target: string | null
  loading: boolean
  disabled: boolean
  nowMs: number
  onAskAgain: (question: string) => void
}) {
  const [filter, setFilter] = useState('')
  const shown = useMemo(() => {
    const query = filter.trim().toLowerCase()
    return query
      ? items.filter((item) => item.question.toLowerCase().includes(query))
      : items
  }, [filter, items])
  const groups = useMemo(() => groupByDay(shown, nowMs), [shown, nowMs])
  if (!loading && items.length === 0) return null
  return (
    <section
      aria-labelledby="ask-earlier-title"
      data-testid="ask-earlier"
      className="mt-12"
    >
      <div className="mb-3 flex items-baseline gap-3">
        <h2
          id="ask-earlier-title"
          className="text-headline-5 text-content-layout-1"
        >
          Earlier
        </h2>
        {items.length > 0 && (
          <Text as="span" level="caption" className="text-content-layout-3">
            {items.length} {items.length === 1 ? 'question' : 'questions'}
            {target ? ` on ${target}` : ''}
          </Text>
        )}
      </div>
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      ) : (
        <>
          {items.length > SEARCH_FROM && (
            <BaseInputText
              name="ask-earlier-search"
              placeholder="Search questions"
              value={filter}
              className="mb-3"
              onChange={(event) => setFilter(event.target.value)}
            />
          )}
          <DividedList columns="1fr auto auto">
            {groups.map((group) => (
              <Fragment key={group.heading}>
                <div className="pt-3.5 pb-1.5 text-label-extra-small font-normal text-content-layout-3">
                  {group.heading}
                </div>
                {group.items.map((item) => (
                  <ListRow key={item.hash} className="min-h-11 py-1.5">
                    <span className="min-w-0 truncate text-body-small text-content-layout-1">
                      {item.question}
                    </span>
                    <span className="text-caption text-content-layout-3">
                      {whenWord(item.last_used, nowMs)}
                    </span>
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Ask again"
                      disabled={disabled}
                      onClick={() => onAskAgain(item.question)}
                    />
                  </ListRow>
                ))}
              </Fragment>
            ))}
          </DividedList>
        </>
      )}
    </section>
  )
}
