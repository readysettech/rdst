// On the Queries tab during the first run: a pointer to Actions, where the first plan lands.
// While it's built it says so; once it's ready it invites a look, until Actions is opened.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import type { ActionsPage } from '@/lib/monitor/actions'
import { buildingFirstPlan, changeCount } from './ActionsTab'

export function FirstPlanPointer({
  page,
  seen,
  onOpenActions,
}: {
  page: ActionsPage | undefined
  /** Actions has been opened since the plan arrived. */
  seen: boolean
  onOpenActions: () => void
}) {
  const building = buildingFirstPlan(page)
  const ready = !building && page?.computed_at != null && !seen
  if (!page || (!building && !ready)) return null
  const { analyzed, total } = page.checkup
  const changes = changeCount(page)
  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-card border px-5 py-4 tablet:flex-row tablet:items-center tablet:justify-between',
        ready
          ? 'border-content-rising-plain/40 bg-surface-rising-soft'
          : 'border-border-layout-1 bg-surface-layout-1'
      )}
      data-testid="first-plan-pointer"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-content-rising-plain mk-breathe"
        />
        <div className="space-y-0.5">
          <Text as="p" level="label-large" className="text-content-layout-1">
            {!ready
              ? 'Building your first plan'
              : changes
                ? `Your first plan is ready: ${changes} ${changes === 1 ? 'change' : 'changes'} worth making`
                : 'Your first plan is in: nothing to change right now'}
          </Text>
          <Text as="p" level="body-small" className="text-content-layout-2">
            {ready
              ? changes
                ? 'Best first, each with the steps and the SQL to run.'
                : 'Checks continue every few minutes. New actions show up here as they turn up.'
              : `Your first actions to improve this database's health show up in Actions as soon as these analyses finish${total ? ` (${Math.min(analyzed, total)} of ${total} done)` : ''}.`}
          </Text>
        </div>
      </div>
      <Button
        label={ready ? 'See the actions' : 'Go to Actions'}
        modifier={ready ? 'solid' : 'outline'}
        size="small"
        icon="arrow-right"
        iconPosition="right"
        onClick={onOpenActions}
      />
    </div>
  )
}
