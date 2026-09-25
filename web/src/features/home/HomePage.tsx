import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Page } from '@rs/ui-new/page'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { useNavigate } from '@tanstack/react-router'
import { DemoCard, JobCard } from './HomeCards'
import { DatabaseHome, FirstRunHome } from './HomeStates'
import { useHomeController } from './useHomeController'

export { DatabaseCard, DatabaseHome, FirstRunHome } from './HomeStates'

function HomeLoading() {
  return (
    <Page className="gap-6">
      <Page.Header>
        <HStack className="w-full items-center gap-4">
          <Skeleton className="h-12 w-12 rounded-2xl" />
          <VStack className="items-start gap-2">
            <Skeleton className="h-6 w-40 rounded-lg" />
            <Skeleton className="h-4 w-80 max-w-full rounded-lg" />
          </VStack>
        </HStack>
      </Page.Header>
      <Page.Content className="space-y-4">
        <Skeleton className="h-64 rounded-2xl" />
        <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
          <Skeleton className="h-36 rounded-2xl" />
          <DemoCard compact />
        </div>
      </Page.Content>
    </Page>
  )
}

export function HomePage() {
  const controller = useHomeController()
  const navigate = useNavigate()

  if (controller.phase === 'loading') return <HomeLoading />

  if (controller.phase === 'error') {
    return (
      <ErrorState
        layout="page"
        errorClass="rdst-service"
        title="Home could not load"
        message="RDST could not confirm the current target state. No database or query data was changed."
        action={{
          label: 'Open the demo',
          onClick: () => navigate({ to: '/demo' }),
        }}
        onRetry={controller.retryCore}
        detail={controller.errorDetail}
      />
    )
  }

  return (
    <Page className="gap-6">
      <Page.Header>
        <HStack className="w-full items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-primary-soft">
            <Icon
              name="dashboard"
              label=""
              className="h-6 w-6 text-content-primary-soft"
            />
          </div>
          <VStack className="min-w-0 flex-1 items-start gap-0.5">
            {/* The sidebar's value-proposition line (C1 / D-5) is the one
              place that says what RDST is for; a description here would print
              the same sentence 300px away. */}
            <Page.Title>Home</Page.Title>
          </VStack>
          <Show when={controller.target}>
            <Tag
              size="base"
              variant="informative"
              modifier="ghost"
              label={controller.target}
            />
          </Show>
        </HStack>
      </Page.Header>

      <Page.Content>
        <Show when={controller.phase === 'degraded'}>
          <VStack className="items-stretch gap-4">
            <ErrorState
              errorClass="database"
              title="Monitoring status didn't load"
              message="RDST couldn't read this database's monitoring state. The Database page may still show it."
              trustworthy="The target remains configured."
              onRetry={controller.retryCore}
              detail={controller.errorDetail}
            />
            <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
              <JobCard
                to="/database"
                icon="database"
                title="Open database"
                description="See live load and the queries worth fixing first."
                chip={{ label: controller.target, variant: 'informative' }}
              />
              <DemoCard compact />
            </div>
          </VStack>
        </Show>
        <Show
          when={
            controller.phase === 'ready' && controller.state === 'first-run'
          }
        >
          <FirstRunHome />
        </Show>
        <Show
          when={
            controller.phase === 'ready' && controller.state !== 'first-run'
              ? controller.state
              : null
          }
        >
          {(state) => (
            <DatabaseHome
              state={state}
              target={controller.target}
              health={controller.health}
              monitorQueries={controller.monitorQueries}
              pulse={controller.pulse}
              monitoringOn={controller.monitoringOn}
              recents={controller.recents}
              counts={controller.counts}
              historyDays={controller.historyDays}
              attentionCount={controller.attentionCount}
            />
          )}
        </Show>
      </Page.Content>
    </Page>
  )
}
