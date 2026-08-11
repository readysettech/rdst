import * as RadixSelect from '@radix-ui/react-select'
import { tv } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
} from 'react'
import { For } from '../../../control-flow/for'
import { useDisclosure } from '../../../hooks/use-disclosure'
import { Icon } from '../../svg/icon'

const selectStyles = tv({
  slots: {
    trigger: [
      'group',
      'flex',
      'h-10',
      'w-full',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-2',
      'transition',
      'duration-fast',
      'ease-base',
      'text-content-layout-1',
      'px-3',
      'py-2',
      'text-body-medium',
      'items-center',
      'justify-between',
      'cursor-pointer',
      '[&>span]:overflow-hidden',
      '[&>span]:text-ellipsis',
      'placeholder:text-content-layout-3',
      'focus-visible:outline-none',
      'focus-visible:shadow-focus',

      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
    ],
    content: [
      'relative',
      'z-50',
      'min-w-(--radix-select-trigger-width)',
      'max-h-80',
      'overflow-hidden',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-1',
      'text-content-layout-1',
    ],
    viewport: [
      'p-1',
      'data-[position=popper]:h-(--radix-select-trigger-height)',
      'data-[position=popper]:w-full',
      'data-[position=popper]:min-w-(--radix-select-trigger-width)',
    ],
    item: [
      'relative',
      'flex',
      'h-10',
      'w-full',
      'select-none',
      'items-center',
      'rounded-lg',
      'py-1.5',
      'px-2',
      'pr-8',
      'text-body-medium',
      'text-content-layout-1',
      'outline-none',
      'transition',
      'duration-fast',
      'ease-base',
      'focus:bg-surface-primary-soft',
      'focus:text-content-layout-1',

      'data-[disabled]:pointer-events-none',
      'data-[disabled]:opacity-50',
    ],
    itemIndicator: [
      'absolute',
      'right-2',
      'flex',
      'h-5',
      'w-5',
      'items-center',
      'justify-center',
    ],
    separator: ['-mx-1', 'my-1', 'h-px', 'bg-border-layout-1'],
  },
})

type SelectRootProps = Omit<
  ComponentPropsWithoutRef<typeof RadixSelect.Root>,
  'value' | 'onValueChange' | 'open' | 'onOpenChange'
>

export type BaseInputSelectProps = SelectRootProps & {
  id?: string
  blockClose?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  options: { value: string | number; label: string; disabled?: boolean }[]
  value?: string
  onValueChange?(value: string): void
  placeholder?: string
  name: string
  disabled?: boolean
  required?: boolean
  loading?: boolean
  triggerClassName?: string
  contentClassName?: string
  viewportClassName?: string
  itemClassName?: string
}

const BaseInputSelect = forwardRef<
  ComponentRef<typeof RadixSelect.Content>,
  BaseInputSelectProps
>(
  (
    {
      id,
      loading,
      disabled,
      name,
      open,
      onOpenChange,
      blockClose,
      placeholder,
      value,
      onValueChange,
      options,
      required,
      triggerClassName,
      contentClassName,
      viewportClassName,
      itemClassName,
      ...restProps
    },
    ref
  ) => {
    const [isOpen, setOpen] = useDisclosure({ open, onOpenChange, blockClose })

    const styles = selectStyles()
    const triggerClasses = styles.trigger({ class: triggerClassName })
    const contentClasses = styles.content({ class: contentClassName })
    const viewportClasses = styles.viewport({ class: viewportClassName })
    const itemClasses = styles.item({ class: itemClassName })

    const isDisabled = Boolean(loading || disabled)

    return (
      <RadixSelect.Root
        name={name}
        open={isOpen}
        onOpenChange={setOpen}
        value={value}
        onValueChange={onValueChange}
        required={required}
        disabled={isDisabled}
        {...restProps}
      >
        <RadixSelect.Trigger
          id={id}
          className={`${triggerClasses} notranslate`}
          translate="no"
        >
          <RadixSelect.Value
            placeholder={placeholder}
            className="flex-1 truncate text-left"
            translate="no"
          />
          <RadixSelect.Icon>
            <Icon name="chevron-up-down" label="Open select" />
          </RadixSelect.Icon>
        </RadixSelect.Trigger>
        <RadixSelect.Portal>
          <RadixSelect.Content
            className={`${contentClasses} notranslate`}
            align="end"
            position="popper"
            translate="no"
            contentEditable={false}
          >
            <RadixSelect.ScrollUpButton className="flex h-6 w-full items-center justify-center text-content-layout-3">
              <Icon name="chevron-up" label="Scroll up" />
            </RadixSelect.ScrollUpButton>
            <RadixSelect.Viewport
              className={`${viewportClasses} notranslate`}
              ref={ref}
              translate="no"
            >
              <For each={options} keyExtractor={(option) => option.value}>
                {(option) => (
                  <RadixSelect.Item
                    value={option.value as any}
                    disabled={option.disabled}
                    className={itemClasses}
                    translate="no"
                  >
                    <RadixSelect.ItemText translate="no">
                      {option.label}
                    </RadixSelect.ItemText>
                    <RadixSelect.ItemIndicator
                      className={styles.itemIndicator()}
                    >
                      <Icon name="tick" label="Selected" />
                    </RadixSelect.ItemIndicator>
                  </RadixSelect.Item>
                )}
              </For>
            </RadixSelect.Viewport>
            <RadixSelect.ScrollDownButton className="flex h-6 w-full items-center justify-center text-content-layout-3">
              <Icon name="chevron-down" label="Scroll down" />
            </RadixSelect.ScrollDownButton>
            <RadixSelect.Arrow className="fill-border-layout-1" />
          </RadixSelect.Content>
        </RadixSelect.Portal>
      </RadixSelect.Root>
    )
  }
)

BaseInputSelect.displayName = 'BaseInputSelect'

export { BaseInputSelect, selectStyles }
