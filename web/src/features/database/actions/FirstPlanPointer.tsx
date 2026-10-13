// On the Live tab during the first run: a pointer to Actions, where the first plan lands.
// While it's built it says so; once it's ready it invites a look, until Actions is opened.
// Analyses held for a Readyset sign-in turn it into a sign-in link.
import { cn } from '@rs/tailwind-base'
import { Button, buttonStyles } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import type { ActionsPage } from '@/lib/monitor/actions'
import { buildingFirstPlan, changeCount } from './actionsModel'

const signInLink = buttonStyles({
  variant: 'primary',
  modifier: 'outline',
  size: 'small',
}).root()

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
  const { analyzed, total, held_text } = page.checkup
  const changes = changeCount(page)
  const held = building && page.checkup.held === 'sign_in'
  return (
    <div
      className="flex flex-col gap-3 border-y border-border-layout-2 py-4 tablet:flex-row tablet:items-center tablet:justify-between"
      data-testid="first-plan-pointer"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={cn(
            'mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-content-rising-plain',
            !ready && !held && 'mk-breathe'
          )}
        />
        <div>
          <Text as="p" level="body-medium" className="text-content-layout-1">
            {held
              ? 'Sign in to run the first analyses'
              : !ready
                ? 'Building your first plan'
                : changes
                  ? `Your first plan is ready: ${changes} ${changes === 1 ? 'change' : 'changes'} worth making`
                  : 'Your first plan is in: nothing to change right now'}
          </Text>
          <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
            {held
              ? 'AI checks need a Readyset sign-in, even with your own key. Your first plan follows once they run.'
              : ready
                ? changes
                  ? 'Best first, each with the steps and the SQL to run.'
                  : 'Checks continue every few minutes. New actions show up in Actions as they turn up.'
                : `Your first actions to improve this database's health show up in Actions as soon as these analyses finish${total ? ` (${Math.min(analyzed, total)} of ${total} done)` : ''}.`}
          </Text>
        </div>
      </div>
      {held ? (
        <Link
          to="/account-login"
          className={signInLink}
          title={held_text ?? undefined}
        >
          Sign in
        </Link>
      ) : (
        <Button
          variant="primary"
          label={ready ? 'See the actions' : 'Go to Actions'}
          modifier={ready ? 'outline' : 'ghost'}
          size="small"
          icon="arrow-right"
          iconPosition="right"
          onClick={onOpenActions}
        />
      )}
    </div>
  )
}
