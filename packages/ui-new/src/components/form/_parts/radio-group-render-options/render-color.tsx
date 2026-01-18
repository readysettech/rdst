import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const renderColorStyles = tv({
  slots: {
    item: [
      'flex',
      'h-10',
      'w-10',
      'items-center',
      'justify-center',
      'rounded-full',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-1',
      'text-label-medium',
      'text-content-layout-3',
      'transition',
      'duration-fast',
      'ease-base',
      'px-3',
      'py-2',
      'cursor-pointer',
      'focus-visible:outline-none',
      'focus-visible:shadow-focus',
      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
      'data-[state=checked]:border-border-primary-solid',
      'data-[state=checked]:text-content-layout-1',
    ],
  },
})

const colorClassMap: Record<string, string> = {
  red: 'bg-(--project-red)',
  plum: 'bg-(--project-plum)',
  violet: 'bg-(--project-violet)',
  blue: 'bg-(--project-blue)',
  green: 'bg-(--project-green)',
  yellow: 'bg-(--project-yellow)',
  amber: 'bg-(--project-amber)',
  brown: 'bg-(--project-brown)',
}

export const renderColor = (option: OptionsType) => {
  const styles = renderColorStyles()
  const backgroundClass = colorClassMap[String(option.value)]

  return (
    <RadioGroup.Item
      value={option.value}
      className={styles.item({ class: backgroundClass })}
      disabled={option.disabled}
    >
      <RadioGroup.Indicator className="flex h-5 w-5 items-center justify-center text-content-layout-1">
        <Icon name="tick" label="Selected" />
      </RadioGroup.Indicator>
    </RadioGroup.Item>
  )
}
