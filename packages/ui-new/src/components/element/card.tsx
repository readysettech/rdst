import { Slot } from '@radix-ui/react-slot'
import { cn, tv, type VariantProps } from '@rs/tailwind-base'

import type { ComponentProps, HTMLAttributes } from 'react'
import type {
  WithAsChild,
  WithChildren,
  WithClassName,
} from '../../helpers/types'
import { HStack, VStack } from './stack'
import { Text } from './text'

export const cardStyles = tv({
  base: [
    'bg-surface-layout-1',
    // TODO: check this syntax
    'border-(length:--border-base)',
    'border-border-layout-1',
    'rounded-[1.25rem]',
    'shadow-none',
    'relative',
  ],
  variants: {
    clickable: {
      true: [
        'cursor-pointer',
        'hover:-translate-y-1',
        'hover:shadow-large',
        '[&_*]:select-none',

        'active:scale-[0.98]',
        'active:origin-center',
        'active:shadow-none',
      ],
    },
    active: {
      true: ['border-border-primary-solid'],
    },
    disabled: {
      true: ['cursor-not-allowed', 'pointer-events-none', 'opacity-50'],
    },
  },
  defaultVariants: {
    clickable: false,
  },
})

type CardVariants = VariantProps<typeof cardStyles>

export type CardProps = CardVariants &
  WithChildren &
  WithAsChild &
  HTMLAttributes<HTMLDivElement>

export const Card = ({
  className,
  active,
  disabled,
  clickable,
  asChild,
  ...restProps
}: CardProps) => {
  const styles = cardStyles({
    active,
    disabled,
    clickable,
    class: className,
  })

  const Comp = asChild ? Slot : 'div'

  return <Comp {...restProps} aria-disabled={disabled} className={styles} />
}

const CardHeader = ({ className, ...rest }: ComponentProps<'div'>) => (
  <VStack
    className={cn(
      'border-b-(length:--border-base) border-b-border-layout-1 gap-1 px-6 py-4 items-start',
      className
    )}
    {...rest}
  />
)

const CardTitle = ({ children, className }: WithChildren & WithClassName) => (
  <Text level="subtitle-1" className={cn('text-balance', className)}>
    {children}
  </Text>
)

const CardDescription = ({
  children,
  className,
}: WithChildren & WithClassName) => (
  <Text level="body-small" className={cn('text-content-layout-3', className)}>
    {children}
  </Text>
)

const CardContent = ({ className, ...rest }: WithChildren & WithClassName) => (
  <div className={cn('p-6', className)} {...rest} />
)

const CardFooter = ({ className, ...rest }: ComponentProps<'div'>) => (
  <HStack
    className={cn(
      [
        'flex items-center justify-end gap-2 px-6 py-4',
        'border-t-(length:--border-base) border-t-border-layout-1',
      ],
      className
    )}
    {...rest}
  />
)

Card.Header = CardHeader
Card.Title = CardTitle
Card.Description = CardDescription
Card.Content = CardContent
Card.Footer = CardFooter
