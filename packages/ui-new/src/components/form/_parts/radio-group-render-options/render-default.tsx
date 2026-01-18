import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { Divider } from '../../../element/divider'
import { HStack, VStack } from '../../../element/stack'
import { Text } from '../../../element/text'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const renderDefaultStyles = tv({
  slots: {
    item: [
      'flex',
      'min-h-10',
      'w-full',
      'items-start',
      'gap-3',
      'rounded-none',
      'cursor-pointer',
      'border-(length:--border-base)',
      'border-transparent',
      'bg-surface-layout-1',
      'px-3',
      'py-2',
      'text-body-medium',
      'text-content-layout-3',
      'transition',
      'duration-fast',
      'ease-base',

      'focus-visible:outline-none',
      'focus-visible:shadow-focus',

      'disabled:cursor-not-allowed',
      'disabled:opacity-50',

      'data-[state=checked]:border-border-primary-solid',
      'data-[state=checked]:bg-surface-primary-soft',
      'data-[state=checked]:text-content-primary-soft',
    ],
    indicator: [
      'mt-1',
      'flex',
      'size-5',
      'shrink-0',
      'items-center',
      'rounded-md',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'text-content-layout-3',
    ],
  },
})

export const renderDefault = (
  option: OptionsType,
  index: number,
  length: number
) => {
  const styles = renderDefaultStyles()

  return (
    <>
      <RadioGroup.Item
        value={option.value}
        className={styles.item()}
        disabled={option.disabled}
      >
        <div className={styles.indicator()}>
          <RadioGroup.Indicator>
            <Icon name="tick" label="Selected" />
          </RadioGroup.Indicator>
        </div>
        <VStack className="w-full items-start gap-1">
          <HStack className="w-full items-center justify-between">
            <Text level="label-medium">{option.label}</Text>
            {option.badge}
          </HStack>
          {option.description && (
            <Text level="caption" className="text-left text-content-layout-3">
              {option.description}
            </Text>
          )}
        </VStack>
      </RadioGroup.Item>
      {index !== length - 1 && <Divider />}
    </>
  )
}
