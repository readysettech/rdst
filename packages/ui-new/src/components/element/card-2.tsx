import { Slot } from '@radix-ui/react-slot'
import { tv, type VariantProps } from '@rs/tailwind-base'
import type { ComponentProps, HTMLAttributes } from 'react'
import type {
  WithAsChild,
  WithChildren,
  WithClassName,
} from '../../helpers/types'
import { stackStyles } from './stack'
import { Text } from './text'

const cardStyles = tv({
  slots: {
    root: [
      'relative',
      'flex',
      'flex-col',
      'gap-1',
      'p-1',
      'rounded-[1.25rem]',
      'bg-surface-layout-soft',
      'shadow-none',
    ],
    header: [
      stackStyles({ variant: 'vstack' }),
      'gap-1',
      'px-6',
      'py-4',
      'items-start',
      'justify-center',
      'min-h-16',
    ],
    title: ['text-balance'],
    description: ['text-content-layout-3'],
    content: [
      'rounded-2xl',
      'border-(length:--border-base)',
      'border-border-layout-soft',
      'bg-surface-layout-1',
      'p-6',
      'rounded-2xl',
    ],
    footer: [
      stackStyles({ variant: 'hstack' }),
      'items-center',
      'justify-end',
      'gap-2',
      'px-6',
      'py-4',
    ],
  },
  variants: {
    clickable: {
      true: {
        root: [
          'cursor-pointer',
          'hover:-translate-y-1',
          'hover:shadow-large',
          'active:scale-[0.98]',
          'active:origin-center',
          'active:shadow-none',
          '[&_*]:select-none',
        ],
      },
    },
  },
  defaultVariants: {
    clickable: false,
  },
})

export type CardProps = VariantProps<typeof cardStyles> &
  WithChildren &
  WithClassName &
  WithAsChild &
  HTMLAttributes<HTMLDivElement>

export const Card = ({ clickable, className, asChild, ...rest }: CardProps) => {
  const styles = cardStyles({ clickable }).root({ class: className })
  const Comp = asChild ? Slot : 'div'
  return <Comp {...rest} className={styles} />
}

const CardHeader = ({
  className,
  ...rest
}: WithClassName & ComponentProps<'div'>) => (
  <div className={cardStyles().header({ class: className })} {...rest} />
)

const CardTitle = ({ children, className }: WithChildren & WithClassName) => (
  <Text level="subtitle-1" className={cardStyles().title({ class: className })}>
    {children}
  </Text>
)
const CardDescription = ({
  children,
  className,
}: WithChildren & WithClassName) => (
  <Text
    level="body-small"
    className={cardStyles().description({ class: className })}
  >
    {children}
  </Text>
)

const CardContent = ({
  className,
  ...rest
}: WithChildren & WithClassName & ComponentProps<'div'>) => (
  <div className={cardStyles().content({ class: className })} {...rest} />
)

const CardFooter = ({
  className,
  ...rest
}: WithClassName & ComponentProps<'div'>) => (
  <div className={cardStyles().footer({ class: className })} {...rest} />
)

Card.Header = CardHeader
Card.Title = CardTitle
Card.Description = CardDescription
Card.Content = CardContent
Card.Footer = CardFooter
