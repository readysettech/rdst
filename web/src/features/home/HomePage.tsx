import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Page } from '@rs/ui-new/page'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link, Navigate } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useMonitoredTargets } from '../../hooks/useMonitoredTargets'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { DatabaseGrid, NextSteps } from './DatabaseGrid'

function HomeFrame({ children }: { children: ReactNode }) {
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
          <Page.Title>Home</Page.Title>
        </HStack>
      </Page.Header>
      <Page.Content className="space-y-8">{children}</Page.Content>
    </Page>
  )
}

function HomeLoading() {
  return (
    <HomeFrame>
      <div className="grid grid-cols-1 gap-4 tablet:grid-cols-2">
        <Skeleton className="h-48 rounded-2xl" />
        <Skeleton className="h-48 rounded-2xl" />
      </div>
    </HomeFrame>
  )
}

/**
 * Home with no database opens Connect. With one monitored database it's that database's
 * page; with two or more it's every database at a glance.
 */
export function HomePage() {
  const status = useSystemStatus()
  const { targets, monitored, isPending } = useMonitoredTargets()

  if (status.isError || status.data?.error)
    return (
      <ErrorState
        layout="page"
        errorClass="rdst-service"
        title="Home couldn't load"
        message="RDST couldn't read your databases. Nothing was changed."
        onRetry={() => void status.refetch()}
        detail={String(status.error ?? status.data?.error ?? '')}
      />
    )
  if (isPending) return <HomeLoading />
  if (targets.length === 0) return <Navigate to="/onboarding" replace />
  if (monitored.length < 2)
    return (
      <Navigate
        to="/database"
        search={monitored.length === 1 ? { target: monitored[0] } : {}}
        replace
      />
    )
  return (
    <HomeFrame>
      <DatabaseGrid targets={targets} monitored={monitored} />
      <NextSteps />
      <Text as="p" level="body-small" className="text-content-layout-3">
        Curious how Readyset caching could speed up your database?{' '}
        <Link
          to="/demo"
          className="text-content-primary-soft underline-offset-4 hover:underline"
        >
          Try the end-to-end demo
        </Link>
      </Text>
    </HomeFrame>
  )
}
