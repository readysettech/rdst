// The report's pointer to Actions: what's open there right now, by kind. Reports describe a
// day; what to change lives on Actions, so the two never disagree.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { useNavigate } from '@tanstack/react-router'
import { useActions, useIndexPlan } from '@/lib/monitor/actions'
import { CARD, Section } from '../reports/snapshotKit'
import { changeCount, flat } from './ActionsTab'
import { CATEGORIES } from './actionsModel'

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
    <Section id="snapshot-actions" title="What to change">
      <div
        className={cn(
          CARD,
          'flex flex-wrap items-center justify-between gap-4 px-5 py-4'
        )}
        data-testid="report-actions-now"
      >
        <div className="space-y-1">
          <p className="text-body-medium text-content-layout-1">
            {total
              ? `${total} ${total === 1 ? 'change is' : 'changes are'} open on Actions right now.`
              : 'Nothing to change right now.'}
          </p>
          <p className="text-body-small text-content-layout-3">
            {counts
              .map((category) => `${category.label} ${category.count}`)
              .join(' · ')}
          </p>
        </div>
        <Button
          label="Open Actions"
          size="small"
          modifier="outline"
          icon="arrow-right"
          iconPosition="right"
          onClick={() =>
            void navigate({
              to: '/database' as never,
              search: { target, view: 'actions' } as never,
            })
          }
        />
      </div>
    </Section>
  )
}
