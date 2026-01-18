import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv, type VariantProps } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type ComponentRef,
  forwardRef,
  type JSX,
  type ReactElement,
  type ReactNode,
} from 'react'
import { For } from '../../../control-flow/for'
import { Divider } from '../../element/divider'
import { HStack } from '../../element/stack'
import { Text } from '../../element/text'
import { renderDefault } from '../_parts/radio-group-render-options/render-default'

const radioGroupStyles = tv({
  base: ['w-full'],
  variants: {
    renderType: {
      default: [
        'grid',
        'rounded-lg',
        'border-(length:--border-base)',
        'border-border-layout-1',
        'bg-surface-layout-1',
        '[&_button:first-of-type]:rounded-t-lg',
        '[&_button:last-of-type]:rounded-b-lg',
      ],
      defaultWithCategory: [
        'grid',
        'rounded-lg',
        'border-(length:--border-base)',
        'border-border-layout-1',
        'bg-surface-layout-1',
        '[&_button:first-of-type]:rounded-t-lg',
        '[&_button:last-of-type]:rounded-b-lg',
      ],
      button: ['grid', 'w-full', 'grid-cols-2', 'gap-2'],
      clusterSize: ['grid', 'gap-2'],
      color: ['flex', 'gap-2'],
      filter: [
        'flex',
        'gap-0',
        'rounded-lg',
        'border-(length:--border-base)',
        'border-border-layout-1',
        'bg-surface-layout-1',
        'overflow-hidden',
      ],
    },
  },
  defaultVariants: {
    renderType: 'default',
  },
})

export type OptionsType = {
  value: string
  label: string
  description?: string
  disabled?: boolean
  badge?: ReactNode
}

export type OptionsTypeWithCategories = {
  category: string
  options: OptionsType[]
}

type RadioGroupVariants = VariantProps<typeof radioGroupStyles>

type RadioGroupRootProps = Omit<
  ComponentPropsWithoutRef<typeof RadioGroup.Root>,
  'value' | 'onValueChange'
>

export type BaseInputRadioGroupProps = RadioGroupRootProps &
  RadioGroupVariants & {
    id?: string
    options: OptionsType[] | OptionsTypeWithCategories[]
    value?: string
    onValueChange?: (value: string) => void
    hideDivider?: boolean
    renderItem?: (
      option: OptionsType,
      index: number,
      length: number
    ) => JSX.Element
  }

const BaseInputRadioGroupInner = (
  {
    id,
    value,
    onValueChange,
    options,
    disabled,
    required,
    renderItem = renderDefault,
    hideDivider,
    renderType,
    className,
    ...restProps
  }: BaseInputRadioGroupProps,
  ref: React.Ref<ComponentRef<typeof RadioGroup.Root>>
) => {
  const styles = radioGroupStyles({ renderType, class: className })

  const isOptionWithCategories = (
    items: OptionsType[] | OptionsTypeWithCategories[]
  ): items is OptionsTypeWithCategories[] => {
    return (
      items.length > 0 &&
      items[0] !== undefined &&
      'category' in items[0] &&
      Boolean(items[0].options?.length)
    )
  }

  return (
    <RadioGroup.Root
      ref={ref}
      id={id}
      value={value}
      onValueChange={onValueChange}
      className={styles}
      disabled={disabled}
      required={required}
      {...restProps}
    >
      {isOptionWithCategories(options) ? (
        <For each={options} keyExtractor={(o) => o.category}>
          {(option) => (
            <>
              <HStack className="px-4 py-2">
                <Text level="overline" className="text-content-layout-3">
                  {option.category}
                </Text>
              </HStack>
              {!hideDivider && <Divider />}
              <For each={option.options} keyExtractor={(o) => o.value}>
                {(subOption, index) =>
                  renderItem(subOption, index, option.options.length)
                }
              </For>
            </>
          )}
        </For>
      ) : (
        <For each={options} keyExtractor={(o) => o.value}>
          {(option, index, array) => (
            <>
              {renderItem(option, index, array.length)}
              {renderType === 'filter' && index !== array.length - 1 && (
                <Divider />
              )}
            </>
          )}
        </For>
      )}
    </RadioGroup.Root>
  )
}

export const BaseInputRadioGroup = forwardRef(BaseInputRadioGroupInner) as (
  props: BaseInputRadioGroupProps & {
    ref?: React.Ref<ComponentRef<typeof RadioGroup.Root>>
  }
) => ReactElement | null

export { radioGroupStyles }
