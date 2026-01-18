import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const renderButtonStyles = tv({
  slots: {
    item: [
      'flex',
      'h-10',
      'w-full',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-1',
      'transition-all',
      'duration-fast',
      'ease-base',
      'text-content-layout-3',

      'items-center',
      'justify-between',
      'px-3',
      'py-2',
      'text-label-medium',
      'cursor-pointer',

      'focus-visible:outline-none',
      'focus-visible:shadow-focus',

      'disabled:opacity-50',
      'disabled:cursor-not-allowed',

      'data-[state=checked]:border-border-primary-solid',
      'data-[state=checked]:text-content-layout-1',
    ],
  },
})

export const renderButton = (option: OptionsType) => {
  const styles = renderButtonStyles()

  return (
    <RadioGroup.Item
      value={option.value}
      className={styles.item()}
      disabled={option.disabled}
    >
      {option.label}
      <RadioGroup.Indicator className="flex h-5 w-5 items-center justify-center text-content-primary-soft">
        <Icon name="tick" label="Selected" />
      </RadioGroup.Indicator>
    </RadioGroup.Item>
  )
}
