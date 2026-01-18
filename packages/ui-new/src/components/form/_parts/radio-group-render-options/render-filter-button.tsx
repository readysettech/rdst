import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import type { OptionsType } from '../../base/input-radio-group'

const filterButtonStyles = tv({
  base: [
    'flex',
    'h-10',
    'w-full',
    'bg-surface-layout-1',
    'transition-all',
    'duration-fast',
    'ease-base',
    'text-label-medium',
    'text-content-layout-3',
    'items-center',
    'justify-between',
    'px-3',
    'py-2',
    'cursor-pointer',

    'focus-visible:outline-none',
    'focus-visible:shadow-focus',

    'disabled:cursor-not-allowed',
    'disabled:opacity-50',

    'data-[state=checked]:border-border-primary-solid',
    'data-[state=checked]:bg-surface-primary-soft',
    'data-[state=checked]:text-content-primary-soft',
  ],
})

export const renderFilterButton = (option: OptionsType) => (
  <RadioGroup.Item
    value={option.value}
    className={filterButtonStyles()}
    disabled={option.disabled}
  >
    {option.label}
  </RadioGroup.Item>
)
