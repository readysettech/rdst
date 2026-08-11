import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Page } from '@rs/ui-new/page'
import { Show } from '@rs/ui-new/show'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { useNavigate } from '@tanstack/react-router'
import { DemoCard, JobCard } from './HomeCards'
import { ActiveHome, ConnectedHome, FirstRunHome } from './HomeStates'
import { useHomeController } from './useHomeController'

export { ActiveHome, ConnectedHome, FirstRunHome } from './HomeStates'

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
      <Page.Content className="grid grid-cols-1 gap-4 desktop:grid-cols-12">
        <Skeleton className="h-72 rounded-2xl desktop:col-span-7" />
        <div className="desktop:col-span-5">
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
            <Page.Title>Home</Page.Title>
            <Page.Description>
              Understand your workload, choose the next action, or experience
              Readyset in the sandbox.
            </Page.Description>
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
              title="Schema readiness could not be confirmed"
              message="RDST can still open the connected target, Query Library, and demo, but Home cannot safely decide whether discovery is complete."
              trustworthy="The target connection remains configured."
              onRetry={controller.retryCore}
              detail={controller.errorDetail}
            />
            <div className="grid grid-cols-1 gap-4 desktop:grid-cols-12">
              <div className="desktop:col-span-7">
                <JobCard
                  to="/queries"
                  icon="observe"
                  title="Open Query Library"
                  description="Continue reviewing the selected target while schema status recovers."
                  chip={{ label: controller.target, variant: 'informative' }}
                />
              </div>
              <div className="desktop:col-span-5">
                <DemoCard compact />
              </div>
            </div>
          </VStack>
        </Show>
        <Show
          when={
            controller.phase === 'ready' && controller.state === 'first-run'
          }
        >
          <FirstRunHome aiKeyState={controller.aiKeyState} />
        </Show>
        <Show
          when={
            controller.phase === 'ready' && controller.state === 'connected'
          }
        >
          <ConnectedHome
            target={controller.target}
            aiKeyState={controller.aiKeyState}
          />
        </Show>
        <Show
          when={controller.phase === 'ready' && controller.state === 'active'}
        >
          <ActiveHome
            target={controller.target}
            counts={controller.counts}
            recents={controller.recents}
            registryState={controller.registryState}
            auditState={controller.auditState}
            lastAuditLabel={controller.lastAuditLabel}
            retentionDays={controller.retentionDays}
            retryRegistry={controller.retryRegistry}
          />
        </Show>
      </Page.Content>
    </Page>
  )
}
