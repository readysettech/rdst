// The Daily report's pointer to Actions: what's open there right now, by kind. The report
// describes a day; what to change lives on Actions, so the two never disagree.
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { useActions, useIndexPlan } from '@/lib/monitor/actions'
import { CATEGORIES, changeCount, flat } from './actionsModel'

export function ActionsNow({ target }: { target: string }) {
  const page = useActions(target).data
  const plan = useIndexPlan(target, true, page?.computed_at).data
  const navigate = useNavigate()
  const cards = flat(page)
  const counts = CATEGORIES.map((category) => ({
    ...category,
    count:
      category.id === 'indexes'
        ? plan
          ? plan.add.length + plan.drop.length
          : 0
        : cards.filter((card) => card.category === category.id).length,
  })).filter((category) => category.id !== 'caching' || category.count)
  const total = changeCount(page, plan)
  return (
    <section aria-labelledby="snapshot-actions">
      <div className="border-b border-border-layout-2 pb-3">
        <h3
          id="snapshot-actions"
          className="text-headline-3 text-content-layout-1"
        >
          What to change
        </h3>
      </div>
      <div
        className="flex flex-wrap items-center justify-between gap-4 border-b border-border-layout-2 py-4"
        data-testid="report-actions-now"
      >
        <div>
          <Text as="p" level="body-medium" className="text-content-layout-1">
            {total
              ? `${total} ${total === 1 ? 'change is' : 'changes are'} open on Actions right now.`
              : 'Nothing to change right now.'}
          </Text>
          <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
            {counts
              .map((category) => `${category.label} ${category.count}`)
              .join(', ')}
          </Text>
        </div>
        <Button
          variant="primary"
          label="Open Actions"
          size="small"
          modifier="ghost"
          icon="arrow-right"
          iconPosition="right"
          onClick={() =>
            void navigate({ to: '/actions', search: { db: target } })
          }
        />
      </div>
    </section>
  )
}
