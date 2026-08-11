import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
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
      className="group rounded-2xl border border-border-layout-1 bg-surface-layout-1 p-5 transition-colors hover:bg-surface-layout-2"
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
            ? 'relative items-start gap-3 p-5'
            : 'relative items-start gap-5 p-7'
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
            className="max-w-xl text-content-rising-solid/80"
          >
            Run the same workload through Postgres and Readyset, then inspect
            the measured speedup. Sandboxed and disposable.
          </Text>
        </VStack>
        <HStack className="items-center gap-2 text-content-rising-solid">
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

export function SetupCard({
  number,
  icon,
  title,
  description,
  action,
}: {
  number: string
  icon: IconStrokeName
  title: string
  description: string
  action: ReactNode
}) {
  return (
    <Card className="h-full">
      <Card.Header className="flex-row items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-raised">
          <Icon name={icon} label="" className="h-5 w-5" />
        </div>
        <VStack className="flex-1 items-start gap-0.5">
          <Text level="caption" className="text-content-layout-3">
            Step {number}
          </Text>
          <Card.Title>{title}</Card.Title>
        </VStack>
      </Card.Header>
      <Card.Content>
        <Text level="body-small" className="text-content-layout-3">
          {description}
        </Text>
      </Card.Content>
      <Card.Footer className="justify-start">{action}</Card.Footer>
    </Card>
  )
}

export function PortfolioTile({
  label,
  value,
  unit,
  to,
  highlight = false,
}: {
  label: string
  value: number
  unit: string
  to: string
  highlight?: boolean
}) {
  return (
    <Link
      to={to}
      className={
        highlight
          ? 'rounded-xl border border-border-primary-soft bg-surface-primary-soft/30 p-4 transition-colors hover:bg-surface-primary-soft/50'
          : 'rounded-xl bg-surface-layout-2/50 p-4 transition-colors hover:bg-surface-raised'
      }
    >
      <VStack className="items-start gap-0.5">
        <Text level="caption" className="text-content-layout-3">
          {label}
        </Text>
        <Text level="headline-5" className="tabular-nums text-content-layout-1">
          {value}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {unit}
        </Text>
      </VStack>
    </Link>
  )
}
