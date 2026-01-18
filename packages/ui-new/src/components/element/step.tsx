import type { ComponentProps, ReactNode } from 'react'
import type { WithChildren } from '../../helpers/types'
import { Spinner } from '../feedback/spinner'
import { Icon, type IconListType } from '../svg/icon'
import { Skeleton } from './skeleton'
import { Center, HStack } from './stack'
import { Text } from './text'

export const Step = ({ children }: WithChildren) => (
  <div className="max-w-[inherit] ml-5">
    <div className="max-w-[inherit] pl-7 border-l-(length:--border-base) border-l-border-layout-1">
      {children}
    </div>
  </div>
)

type StepItemProps = {
  number?: number
  loading?: boolean
  label: string
  info?: string
  iconName?: IconListType
  labelChildren?: ReactNode
} & Partial<WithChildren> &
  Omit<ComponentProps<'div'>, 'children'>

const StepItem = ({
  number,
  label,
  info,
  children,
  loading,
  iconName = 'tick-double',
  labelChildren,
  ...props
}: StepItemProps) => (
  <>
    <HStack className="items-start mb-2 -ml-12 mt-4" {...props}>
      <Center className="bg-surface-layout-1 rounded-full w-10 min-w-10 h-10 text-label-medium text-content-layout-2 border-(length:--border-base) border-border-layout-1">
        {loading ? (
          <Spinner color="layout" />
        ) : (
          (number ?? <Icon name={iconName} label={label} />)
        )}
      </Center>
      <Text level="subtitle-1" className="relative pt-2 w-full">
        {label}
      </Text>
      {labelChildren}
    </HStack>
    {children}
    {info && (
      <Text level="caption" className="text-content-layout-3 relative pt-2">
        {info}
      </Text>
    )}
  </>
)

export const RootSkeleton = ({ children }: WithChildren) => (
  <div className="max-w-[inherit] ml-5">
    <div className="max-w-[inherit] pl-7 border-l-(length:--border-base) border-l-transparent">
      {children}
    </div>
  </div>
)

const StepItemSkeleton = () => (
  <HStack className="items-center mb-2 -ml-12 mt-4">
    <Skeleton className="rounded-full w-10 h-10" />
    <Skeleton className="h-6 w-64" />
  </HStack>
)

Step.Item = StepItem
Step.Skeleton = RootSkeleton
Step.ItemSkeleton = StepItemSkeleton
