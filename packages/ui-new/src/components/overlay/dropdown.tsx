import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { cn, tv, type VariantProps } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Link } from '@tanstack/react-router'
import { AnimatePresence, m } from 'motion/react'
import {
  Children,
  type ComponentPropsWithoutRef,
  type ComponentRef,
  cloneElement,
  forwardRef,
  type HTMLAttributeAnchorTarget,
  isValidElement,
  type ReactElement,
} from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'
import { useDisclosure } from '../../hooks/use-disclosure'
import { getTransition } from '../../motion/transition'
import { HStack } from '../element/stack'
import { Icon } from '../svg/icon'

const dropdownContentStyles = tv({
  base: [
    'z-50',
    'min-w-32',
    'w-full',
    'overflow-hidden',
    'rounded-xl',
    'border-(length:--border-base)',
    'border-border-layout-1',
    'bg-surface-layout-1',
    'py-2',
    'text-content-layout-2',
    'shadow-lg',
  ],
})

type DropdownChildProps = {
  open?: boolean
}

type DropdownProps = DropdownMenu.DropdownMenuProps & {
  blockClose?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export const Dropdown = ({
  open,
  blockClose,
  onOpenChange,
  ...props
}: DropdownProps) => {
  const [isOpen, setOpen] = useDisclosure({ open, onOpenChange, blockClose })

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={setOpen} {...props}>
      {Children.map(props.children, (child) => {
        if (isValidElement(child)) {
          return cloneElement(child as ReactElement<DropdownChildProps>, {
            open: isOpen,
          })
        }
        return child
      })}
    </DropdownMenu.Root>
  )
}

type DropdownContentProps = {
  title?: string
  description?: string
  open?: boolean
  layoutId?: string
} & ComponentPropsWithoutRef<typeof DropdownMenu.Content> &
  WithClassName

const DropdownContent = forwardRef<
  ComponentRef<typeof DropdownMenu.Content>,
  DropdownContentProps
>(
  (
    { className, children, title, description, open, layoutId, ...props },
    ref
  ) => {
    const transition = getTransition()

    const styles = dropdownContentStyles({
      className: [className, 'notranslate'],
    })

    return (
      <AnimatePresence>
        {open && (
          <DropdownMenu.Portal forceMount>
            <DropdownMenu.Content
              ref={ref}
              className={styles}
              align="start"
              sideOffset={8}
              translate="no"
              contentEditable={false}
              {...props}
              asChild
            >
              <m.div
                layoutId={layoutId}
                initial={{ scale: 0.9, opacity: 0, y: 20 }}
                animate={{ scale: 1, opacity: 1, y: 0 }}
                exit={{ scale: 0.9, opacity: 0, y: 20 }}
                transition={transition}
              >
                {children}
              </m.div>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        )}
      </AnimatePresence>
    )
  }
)

const dropdownItemStyles = tv({
  base: [
    'relative',
    'flex',
    'cursor-pointer',
    'select-none',
    'items-center',
    'justify-between',
    'rounded-none',
    'px-4',
    'py-1',
    'h-10',
    'min-w-60',
    'text-label-large',
    'outline-none',
    'transition-[colors,background]',
    'duration-fast',
    'ease-base',
    'text-content-layout-2',
    'gap-2',
    'hover:bg-surface-primary-soft',
    'hover:text-content-primary-soft',
    'focus:bg-surface-primary-soft',
    'focus:text-content-primary-soft',
    'data-disabled:pointer-events-none',
    'data-disabled:opacity-50',
  ],
  variants: {
    active: {
      true: ['bg-surface-primary-soft-hover', 'text-content-primary-soft'],
    },
  },
})

export type BaseDropdownItemProps = {
  leftIcon?: IconStrokeName
  rightIcon?: IconStrokeName
  label: string
  classMerge?: string
  href?: string
  target?: HTMLAttributeAnchorTarget | undefined
} & Omit<ComponentPropsWithoutRef<typeof DropdownMenu.Item>, 'children'> &
  VariantProps<typeof dropdownItemStyles> &
  WithClassName &
  WithChildren

export type DropdownItemProps = BaseDropdownItemProps

const DropdownItem = forwardRef<
  ComponentRef<typeof DropdownMenu.Content>,
  DropdownItemProps
>((props, ref) => {
  const {
    active,
    className,
    leftIcon,
    rightIcon,
    label,
    href,
    target,
    ...restProps
  } = props
  const styles = dropdownItemStyles({ active, className })

  if ('href' in props && href !== undefined) {
    return (
      <DropdownMenu.Item ref={ref} className={styles} {...restProps} asChild>
        <a
          href={href}
          target={target}
          className={className}
          ref={ref as React.Ref<HTMLAnchorElement>}
          rel="noopener noreferrer"
        >
          <HStack>
            {leftIcon && <Icon name={leftIcon} label={label} />}
            {label}
          </HStack>
          {rightIcon && <Icon name={rightIcon} label={label} />}
        </a>
      </DropdownMenu.Item>
    )
  }

  return (
    <DropdownMenu.Item ref={ref} className={styles} {...restProps}>
      <HStack>
        {leftIcon && <Icon name={leftIcon} label={label} />}
        {label}
      </HStack>
      {rightIcon && <Icon name={rightIcon} label={label} />}
    </DropdownMenu.Item>
  )
})

const DropdownItemWithChildren = forwardRef<
  ComponentRef<typeof DropdownMenu.Content>,
  Omit<DropdownItemProps, 'label'>
>((props, ref) => {
  const { active, className, children, ...restProps } = props

  const styles = dropdownItemStyles({ active, className })

  if ('href' in props && props.href !== undefined) {
    return (
      <DropdownMenu.Item ref={ref} className={styles} {...restProps} asChild>
        <Link
          to={props.href!}
          target={props.target}
          className={styles}
          ref={ref as React.Ref<HTMLAnchorElement>}
        >
          {children && typeof children !== 'bigint' ? children : null}
        </Link>
      </DropdownMenu.Item>
    )
  }

  return (
    <DropdownMenu.Item ref={ref} className={styles} {...restProps}>
      {children && typeof children !== 'bigint' ? children : null}
    </DropdownMenu.Item>
  )
})

type DropdownSeparatorProps = {} & ComponentPropsWithoutRef<
  typeof DropdownMenu.Separator
> &
  VariantProps<typeof dropdownItemStyles>

const DropdownSeparator = forwardRef<
  ComponentRef<typeof DropdownMenu.Separator>,
  DropdownSeparatorProps
>(({ active, ...props }, ref) => (
  <DropdownMenu.Separator
    ref={ref}
    className={cn('h-px', 'my-2', 'bg-border-layout-1')}
    {...props}
  />
))

type DropdownLabelProps = {
  classMerge?: string
} & ComponentPropsWithoutRef<typeof DropdownMenu.Label> &
  WithClassName

const labelStyles = tv({
  base: [
    'text-subtitle-2',
    'px-4',
    'py-3',
    'border-b-border-layout-1',
    'border-b-(length:--border-base)',
    'text-content-layout-3',
  ],
})

export const DropdownLabel = ({ className, ...props }: DropdownLabelProps) => (
  <DropdownMenu.Label
    {...props}
    className={labelStyles({ class: className })}
  />
)

Dropdown.Content = DropdownContent
Dropdown.Item = DropdownItem
Dropdown.ItemWithChildren = DropdownItemWithChildren
Dropdown.Separator = DropdownSeparator

Dropdown.Label = DropdownLabel
Dropdown.Trigger = DropdownMenu.Trigger
Dropdown.Group = DropdownMenu.Group
Dropdown.Arrow = DropdownMenu.Arrow
