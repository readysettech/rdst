import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { cn, tv, type VariantProps } from '@rs/tailwind-base'

import { AnimatePresence, m } from 'motion/react'
import {
  Children,
  type ComponentPropsWithoutRef,
  type ComponentRef,
  cloneElement,
  forwardRef,
  isValidElement,
  type ReactElement,
} from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'
import { useDisclosure } from '../../hooks/use-disclosure'
import { getTransition } from '../../motion/transition'
import { HStack } from '../element/stack'
import { BaseInputCheckbox } from '../form/base/input-checkbox'
import { Icon, type IconListType } from '../svg/icon'

// Base content styling recipe
const dropdownContentStyles = tv({
  base: [
    'z-50',
    'min-w-32',
    'max-w-5xl',
    'w-full',
    'overflow-hidden',
    'rounded-xl',
    'border-(length:--border-base)',
    'border-border-layout-1',
    'bg-surface-layout-1',
    'py-1',
    'px-1',
    'text-content-layout-2',
    'shadow-large',
  ],
})

// Base item styling recipe
const dropdownItemStyles = tv({
  base: [
    'relative',
    'flex',
    'h-10',
    'min-w-60',
    'items-center',
    'justify-between',
    'gap-2',
    'px-4',
    'py-1',
    'rounded-xl',
    'text-label-large',
    'text-content-layout-2',
    'cursor-pointer',
    'select-none',
    'outline-none',
    'focus-visible:outline-none',
    'transition-colors',
    'duration-fast',
    'ease-base',
    'hover:bg-surface-primary-soft-hover',
    'hover:text-content-primary-soft',
    'focus:bg-surface-primary-soft-hover',
    'focus:text-content-primary-soft',
    'data-[disabled]:pointer-events-none',
    'data-[disabled]:opacity-50',
  ],
  variants: {
    active: {
      true: ['bg-surface-primary-soft-hover', 'text-content-primary-soft'],
    },
  },
})

// Dropdown Root
export const Dropdown = (
  props: DropdownMenu.DropdownMenuProps & { blockClose?: boolean }
) => {
  const [isOpen, setOpen] = useDisclosure({
    open: props.open,
    onOpenChange: props.onOpenChange,
    blockClose: props.blockClose,
  })
  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={setOpen} {...props}>
      {Children.map(props.children, (child) =>
        isValidElement(child)
          ? cloneElement(child as ReactElement<{ open: boolean }>, {
              open: isOpen,
            })
          : child
      )}
    </DropdownMenu.Root>
  )
}

// Generic Content (for Content & SubContent)
export const DropdownContent = forwardRef<
  ComponentRef<typeof DropdownMenu.Content>,
  ComponentPropsWithoutRef<typeof DropdownMenu.Content> &
    WithClassName & { open?: boolean }
>(({ className, open, children, ...rest }, ref) => {
  const transition = getTransition()
  const styles = dropdownContentStyles({ class: [className, 'notranslate'] })

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
            {...rest}
            asChild
          >
            <m.div
              initial={{ opacity: 0, translateY: '10%', filter: 'blur(8px)' }}
              animate={{ opacity: 1, translateY: '0%', filter: 'blur(0px)' }}
              exit={{
                opacity: 0,
                translateY: '5%',
                filter: 'blur(8px)',
                transition: { ...transition, duration: 0.15 },
              }}
              transition={transition}
            >
              {children}
            </m.div>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      )}
    </AnimatePresence>
  )
})

export const DropdownSubContent = forwardRef<
  ComponentRef<typeof DropdownMenu.Content>,
  ComponentPropsWithoutRef<typeof DropdownMenu.Content> &
    WithClassName & { open?: boolean }
>(({ className, open, children, ...rest }, ref) => {
  const transition = getTransition()
  const styles = dropdownContentStyles({ class: [className, 'notranslate'] })

  return (
    <AnimatePresence>
      {open && (
        <DropdownMenu.Portal forceMount>
          <DropdownMenu.SubContent
            ref={ref}
            className={styles}
            align="start"
            sideOffset={8}
            translate="no"
            contentEditable={false}
            {...rest}
            asChild
          >
            <m.div
              initial={{ opacity: 0, translateY: '10%', filter: 'blur(8px)' }}
              animate={{ opacity: 1, translateY: '0%', filter: 'blur(0px)' }}
              exit={{
                opacity: 0,
                translateY: '5%',
                filter: 'blur(8px)',
                transition: { ...transition, duration: 0.15 },
              }}
              transition={transition}
            >
              {children}
            </m.div>
          </DropdownMenu.SubContent>
        </DropdownMenu.Portal>
      )}
    </AnimatePresence>
  )
})

// Item components
const BaseItem = forwardRef<
  ComponentRef<typeof DropdownMenu.Item>,
  ComponentPropsWithoutRef<typeof DropdownMenu.Item> &
    VariantProps<typeof dropdownItemStyles> &
    WithClassName & { leftIcon?: IconListType; rightIcon?: IconListType }
>((props, ref) => {
  const { active, className, leftIcon, rightIcon, children, ...restProps } =
    props
  const styles = dropdownItemStyles({ active, className })
  return (
    <DropdownMenu.Item ref={ref} className={styles} {...restProps}>
      <HStack>
        {leftIcon && <Icon name={leftIcon} label={String(children)} />}
        {children}
      </HStack>
      {rightIcon && <Icon name={rightIcon} label={String(children)} />}
    </DropdownMenu.Item>
  )
})
export const DropdownItem = BaseItem

type DropdownCheckboxItemProps = {
  checked: boolean
  id: string
  onClick: () => void
} & WithClassName &
  WithChildren

export const DropdownCheckboxItem = forwardRef<
  HTMLLabelElement,
  DropdownCheckboxItemProps
>(({ checked, id, className, onClick, children }, ref) => (
  <label
    htmlFor={id}
    ref={ref}
    className={dropdownItemStyles({ className })}
    onClick={onClick}
    onKeyUp={onClick}
    onKeyDown={onClick}
  >
    <HStack>
      <BaseInputCheckbox checked={checked} />
      {children}
    </HStack>
  </label>
))
export const DropdownRadioGroup = DropdownMenu.RadioGroup
export const DropdownRadioItem = forwardRef<
  ComponentRef<typeof DropdownMenu.RadioItem>,
  ComponentPropsWithoutRef<typeof DropdownMenu.RadioItem> & WithClassName
>(({ className, ...props }, ref) => (
  <DropdownMenu.RadioItem
    ref={ref}
    className={dropdownItemStyles({ className })}
    {...props}
  >
    <HStack>
      <DropdownMenu.ItemIndicator>
        <Icon name="building" label="selected" />
      </DropdownMenu.ItemIndicator>
      {props.children}
    </HStack>
  </DropdownMenu.RadioItem>
))

// Other primitives
export const DropdownTrigger = DropdownMenu.Trigger
export const DropdownSeparator = forwardRef<
  ComponentRef<typeof DropdownMenu.Separator>,
  ComponentPropsWithoutRef<typeof DropdownMenu.Separator>
>((props, ref) => (
  <DropdownMenu.Separator
    ref={ref}
    className={cn('h-px', 'my-2', 'bg-border-layout-1')}
    {...props}
  />
))
export const DropdownLabel = forwardRef<
  ComponentRef<typeof DropdownMenu.Label>,
  ComponentPropsWithoutRef<typeof DropdownMenu.Label> & WithClassName
>(({ className, ...props }, ref) => (
  <DropdownMenu.Label
    ref={ref}
    className={cn(
      [
        'text-subtitle-2',
        'px-4',
        'py-3',
        'border-b-(length:--border-base)',
        'border-border-layout-1',
        'text-content-layout-3',
      ],
      className
    )}
    {...props}
  />
))
export const DropdownGroup = DropdownMenu.Group
export const DropdownArrow = DropdownMenu.Arrow
export const DropdownSub = (props: DropdownMenu.DropdownMenuSubProps) => {
  const [isOpen, setOpen] = useDisclosure({
    open: props.open,
    onOpenChange: props.onOpenChange,
  })
  return (
    <DropdownMenu.Sub open={isOpen} onOpenChange={setOpen} {...props}>
      {Children.map(props.children, (child) =>
        isValidElement(child)
          ? cloneElement(child as ReactElement<{ open: boolean }>, {
              open: isOpen,
            })
          : child
      )}
    </DropdownMenu.Sub>
  )
}
export const DropdownSubTrigger = DropdownMenu.SubTrigger
