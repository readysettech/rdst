import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import { AnimatedSurfaceBackdrop } from '../../components/AnimatedSurfaceBackdrop'

export interface JobCardProps {
  to: string
  icon: IconStrokeName
  title: string
  description: string
  chip?: {
    label: string
    variant: 'positive' | 'warning' | 'informative' | 'primary' | 'neutral'
  }
}

export function JobCard({ to, icon, title, description, chip }: JobCardProps) {
  return (
    <Link
      to={to}
      className="group block h-full rounded-2xl border border-border-layout-1 bg-surface-layout-1 p-5 transition-colors hover:bg-surface-layout-2"
    >
      <VStack className="h-full items-start gap-3">
        <HStack className="w-full items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface-raised">
            <Icon
              name={icon}
              label=""
              className="h-5 w-5 text-content-layout-1"
            />
          </div>
          <Text level="label-large" className="flex-1 text-content-layout-1">
            {title}
          </Text>
          <Icon
            name="arrow-right"
            label=""
            className="h-4 w-4 text-content-layout-3 transition-transform group-hover:translate-x-0.5"
          />
        </HStack>
        <Text level="body-small" className="text-content-layout-3">
          {description}
        </Text>
        <Show when={chip}>
          {(value) => (
            <Tag
              size="small"
              variant={value.variant}
              modifier="ghost"
              label={value.label}
            />
          )}
        </Show>
      </VStack>
    </Link>
  )
}

export function DemoCard({ compact = false }: { compact?: boolean }) {
  return (
    <Link
      to="/demo"
      className="group relative block h-full overflow-hidden rounded-2xl bg-surface-rising-solid shadow-elevation-2"
    >
      <AnimatedSurfaceBackdrop palette="purple" />
      <VStack
        className={
          compact
            ? 'relative h-full items-start gap-3 p-5'
            : 'relative h-full items-start gap-5 p-7'
        }
      >
        <HStack className="w-full items-center justify-between gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-layout-1/20">
            <Icon
              name="querypilot"
              label=""
              className="h-6 w-6 text-content-rising-solid"
            />
          </div>
          <Tag
            size="small"
            variant="rising"
            modifier="solid"
            label="No setup"
          />
        </HStack>
        <VStack className="items-start gap-1">
          <Text
            level={compact ? 'label-large' : 'headline-4'}
            className="text-content-rising-solid"
          >
            See Readyset under real load
          </Text>
          <Text
            level="body-small"
            className="max-w-xl text-content-rising-solid"
          >
            Run a sample workload against a demo Postgres, then through
            Readyset, and see how much faster it gets. It runs in a sandbox and
            cleans up after itself.
          </Text>
        </VStack>
        <HStack className="mt-auto items-center gap-2 text-content-rising-solid">
          <Text level="label-medium">Launch the demo</Text>
          <Icon
            name="arrow-right"
            label=""
            className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
          />
        </HStack>
      </VStack>
    </Link>
  )
}

/**
 * The demo as a wide strip near the top of Home: one line of what it is and a clear
 * way in, short enough to keep the database card above the fold.
 */
export function DemoBanner() {
  return (
    <Link
      to="/demo"
      data-testid="home-demo-banner"
      className="group relative block overflow-hidden rounded-2xl bg-surface-rising-solid shadow-elevation-2"
    >
      <AnimatedSurfaceBackdrop palette="purple" />
      <div className="relative flex flex-col items-stretch gap-4 px-5 py-4 tablet:flex-row tablet:items-center tablet:gap-5 tablet:px-6">
        <div className="flex min-w-0 flex-1 items-start gap-4 tablet:items-center">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-layout-1/20">
            <Icon
              name="querypilot"
              label=""
              className="h-6 w-6 text-content-rising-solid"
            />
          </div>
          <VStack className="min-w-0 flex-1 items-start gap-0.5">
            <HStack className="items-center gap-2">
              <Text level="label-large" className="text-content-rising-solid">
                Try the demo
              </Text>
              <Tag
                size="small"
                variant="rising"
                modifier="solid"
                label="No setup"
              />
            </HStack>
            <Text level="body-small" className="text-content-rising-solid">
              Run a sample workload against a demo Postgres database, then
              through Readyset, and see how much more throughput it gets. It all
              runs in a temporary sandbox that's removed when you're done.
            </Text>
          </VStack>
        </div>
        <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-pill bg-surface-layout-1 px-4 py-2 text-label-medium text-content-layout-1 shadow-elevation-1 transition-transform duration-fast group-hover:translate-x-0.5">
          Launch the demo
          <Icon
            name="arrow-right"
            label=""
            aria-hidden="true"
            className="h-4 w-4"
          />
        </span>
      </div>
    </Link>
  )
}
