import { Button } from '@rs/ui-new/button'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { AnimatedSurfaceBackdrop } from '../../../components/AnimatedSurfaceBackdrop'
import type { QueryLibraryController } from './useQueryLibraryController'

function queryNoun(count: number) {
  return count === 1 ? 'query' : 'queries'
}

/**
 * Name the pending rows by lifecycle: rows first observed now are "new",
 * rows whose existing entry gained fresh evidence are "updated".
 */
function pendingPhrase(newCount: number, updatedCount: number) {
  if (newCount > 0 && updatedCount > 0) {
    return `${newCount} new, ${updatedCount} updated`
  }
  if (newCount > 0) return `${newCount} new ${queryNoun(newCount)}`
  return `${updatedCount} updated ${queryNoun(updatedCount)}`
}

function pendingHeadline(newCount: number, updatedCount: number) {
  if (newCount > 0 && updatedCount > 0) {
    return `${newCount} new, ${updatedCount} updated queries`
  }
  if (newCount > 0) return `${newCount} new ${queryNoun(newCount)} discovered`
  return `${updatedCount} ${queryNoun(updatedCount)} updated`
}

export function QueryLibraryNewQueriesCard({
  controller,
}: {
  controller: QueryLibraryController
}) {
  const { library, registry } = controller
  const { pendingNewCount, pendingUpdatedCount } = library
  const reviewCount = library.newVisibleCount
  const hasPending = pendingNewCount + pendingUpdatedCount > 0
  const hasReviewable = reviewCount > 0

  if (!hasPending && !hasReviewable) return null

  return (
    <div className="relative overflow-hidden rounded-2xl bg-surface-rising-solid px-6 py-5 shadow-elevation-2">
      <AnimatedSurfaceBackdrop palette="purple" />

      <HStack className="relative items-center justify-between gap-5 flex-wrap">
        <VStack className="min-w-0 flex-1 items-start gap-1">
          <Text
            level="overline"
            className="text-content-rising-solid/80 uppercase tracking-wider"
          >
            Workload update
          </Text>
          <Text
            as="h2"
            level="headline-4"
            className="text-content-rising-solid"
          >
            {hasPending
              ? pendingHeadline(pendingNewCount, pendingUpdatedCount)
              : `${reviewCount} new ${queryNoun(reviewCount)} ready to review`}
          </Text>
          <Text level="body-small" className="text-content-rising-solid/80">
            {hasPending
              ? 'Readyset kept your current list still while these queries arrived.'
              : 'Review the new workload evidence, then clear the update when you are done.'}
          </Text>
        </VStack>

        <HStack className="items-center gap-2 flex-wrap">
          <Show when={hasReviewable}>
            <Button
              variant="primary"
              modifier="ghost"
              icon="tick"
              iconPosition="left"
              label="Mark all reviewed"
              loading={registry.markReviewedMutation.isPending}
              onClick={library.markAllReviewed}
              className="text-content-rising-solid hover:bg-surface-layout-1/10 active:bg-surface-layout-1/15"
            />
          </Show>
          <Show when={hasPending}>
            <Button
              variant="primary"
              modifier="solid"
              icon="add"
              iconPosition="left"
              label={`Show ${pendingPhrase(pendingNewCount, pendingUpdatedCount)}`}
              onClick={library.revealPending}
              className="bg-surface-layout-1 text-content-layout-1 hover:bg-surface-layout-2 active:bg-surface-layout-2"
            />
          </Show>
        </HStack>
      </HStack>
    </div>
  )
}
