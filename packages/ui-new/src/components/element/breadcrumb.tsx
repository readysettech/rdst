import { Slot } from '@radix-ui/react-slot'
import { tv } from '@rs/tailwind-base'
import {
  type ComponentProps,
  type ComponentPropsWithoutRef,
  forwardRef,
  type ReactNode,
} from 'react'
import { Icon } from '../svg/icon'

const breadcrumbStyles = tv({
  slots: {
    list: [
      'flex',
      'flex-wrap',
      'items-center',
      'gap-1',
      'text-label-small',
      'text-content-layout-3',
      'wrap-break-word',
      'tablet:gap-2',
    ],
    item: ['inline-flex', 'items-center', 'gap-1.5', 'tablet:gap-2.5'],
    link: [
      'max-w-44',
      'select-none',
      'truncate',
      'transition-colors',
      'overflow-hidden',
      'text-ellipsis',
      'whitespace-nowrap',
      'max-w-44',
      'duration-slower',
      'ease-base',
      'hover:text-content-layout-1',
    ],
    page: [
      'max-w-44',
      'cursor-default',
      'overflow-hidden',
      'text-ellipsis',
      'whitespace-nowrap',
      'text-content-layout-1',
    ],
    ellipsis: ['flex', 'h-9', 'w-9', 'items-center', 'justify-center'],
  },
})

const breadcrumbSlots = breadcrumbStyles()

const Breadcrumb = forwardRef<
  HTMLElement,
  ComponentPropsWithoutRef<'nav'> & {
    separator?: ReactNode
  }
>((props, ref) => <nav ref={ref} aria-label="breadcrumb" {...props} />)

const BreadcrumbList = forwardRef<
  HTMLOListElement,
  ComponentPropsWithoutRef<'ol'>
>(({ className, ...props }, ref) => (
  <ol
    ref={ref}
    className={breadcrumbSlots.list({ class: className })}
    {...props}
  />
))

const BreadcrumbItem = forwardRef<
  HTMLLIElement,
  ComponentPropsWithoutRef<'li'>
>(({ className, ...props }, ref) => (
  <li
    ref={ref}
    className={breadcrumbSlots.item({ class: className })}
    {...props}
  />
))

const BreadcrumbLink = forwardRef<
  HTMLAnchorElement,
  ComponentPropsWithoutRef<'a'> & {
    asChild?: boolean
  }
>(({ asChild, className, ...props }, ref) => {
  const Comp = asChild ? Slot : 'a'

  return (
    <Comp
      ref={ref}
      className={breadcrumbSlots.link({ class: className })}
      {...props}
    />
  )
})

const BreadcrumbPage = forwardRef<
  HTMLSpanElement,
  Omit<ComponentPropsWithoutRef<'span'>, 'className'>
>((props, ref) => (
  // biome-ignore lint/a11y/useFocusableInteractive: <!>
  // biome-ignore lint/a11y/useSemanticElements: <!>
  <span
    ref={ref}
    role="link"
    aria-disabled="true"
    aria-current="page"
    className="max-w-44 cursor-default truncate text-content-layout-1"
    {...props}
  />
))

const BreadcrumbSeparator = ({ children, ...props }: ComponentProps<'li'>) => (
  <li role="presentation" aria-hidden="true" {...props}>
    {children ?? <Icon name="chevron-right" label="Breadcrumb Separator" />}
  </li>
)

const BreadcrumbEllipsis = ({
  className,
  ...props
}: ComponentProps<'span'>) => (
  <span
    role="presentation"
    aria-hidden="true"
    className={breadcrumbSlots.ellipsis({ class: className })}
    {...props}
  >
    <Icon name="more" label="More Item" />
    <span className="sr-only">More</span>
  </span>
)

export {
  Breadcrumb,
  BreadcrumbEllipsis,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
}
