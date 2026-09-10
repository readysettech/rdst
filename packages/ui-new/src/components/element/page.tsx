import { cn } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'
import { Container, type ContainerProps } from './container'
import { Stack } from './stack'
import { Text } from './text'

export const Page = ({ className, ...props }: ContainerProps) => (
  <Container
    className={cn('flex', 'flex-col', 'gap-2', className)}
    {...props}
  />
)

const PageHeader = ({ className, ...props }: ComponentProps<'div'>) => (
  <Stack
    className={cn([
      'gap-1',
      'w-full',
      'pt-8',
      'pb-4',
      'items-start',
      className,
    ])}
    {...props}
  />
)

const PageContent = ({ className, ...props }: ComponentProps<'div'>) => (
  <div className={cn(['w-full'], className)} {...props} />
)

// The page title is the route's level-1 heading: it is the first thing a
// screen-reader user jumps to, and the only heading a page is guaranteed to
// have.
const PageTitle = ({ children, className }: WithChildren & WithClassName) => (
  <Text
    level="headline-3"
    as="h1"
    className={cn('h-10', 'leading-10', className)}
  >
    {children}
  </Text>
)

const PageDescription = ({
  children,
  className,
}: WithChildren & WithClassName) => (
  <Text level="body-small" className={cn('text-content-layout-3', className)}>
    {children}
  </Text>
)

Page.Header = PageHeader
Page.Title = PageTitle
Page.Description = PageDescription
Page.Content = PageContent
