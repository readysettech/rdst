import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { formatMoney } from '../../../../helpers/format'
import { HStack, VStack } from '../../../element/stack'
import { Tag } from '../../../element/tag'
import { Text } from '../../../element/text'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const clusterSizeStyles = tv({
  slots: {
    item: [
      'flex',
      'h-auto',
      'w-full',
      'flex-col',
      'items-start',
      'overflow-hidden',
      'rounded-lg',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'bg-surface-layout-2',
      'text-body-medium',
      'text-content-layout-3',
      'transition',
      'duration-fast',
      'ease-base',
      'focus-visible:outline-none',
      'focus-visible:ring-2',
      'focus-visible:ring-border-primary-soft',
      'focus-visible:ring-offset-2',
      'focus-visible:ring-offset-surface-layout-1',
      'disabled:cursor-not-allowed',
      'disabled:opacity-50',
      'data-[state=checked]:border-border-primary-solid',
      'data-[state=checked]:bg-surface-layout-1',
      'data-[state=checked]:text-content-primary-soft',
    ],
    indicator: [
      'flex',
      'h-6',
      'w-6',
      'items-center',
      'justify-center',
      'rounded-md',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'text-content-layout-3',
    ],
  },
})

type CustomOptionsType = OptionsType & {
  price?: number
  available?: boolean
  descriptions?: string[]
}

export const renderClusterSize = (option: CustomOptionsType) => {
  const styles = clusterSizeStyles()

  return (
    <RadioGroup.Item
      value={option.value}
      className={styles.item()}
      disabled={option.disabled}
    >
      <HStack className="w-full items-start gap-3 p-3">
        <div className={styles.indicator()}>
          <RadioGroup.Indicator>
            <Icon name="tick" label="Selected" />
          </RadioGroup.Indicator>
        </div>
        <VStack className="w-full items-start gap-1">
          <HStack className="w-full items-center justify-between gap-2">
            <HStack className="items-center gap-2">
              <Text level="label-medium">{option.label}</Text>
              {option.available === false && (
                <Tag
                  label="Purchase"
                  size="small"
                  variant="rising"
                  modifier="outline"
                />
              )}
            </HStack>
            {option.price ? (
              <HStack className="items-baseline gap-1">
                <Text level="subtitle-1">{formatMoney(option.price)}</Text>
                <Text level="caption" className="text-content-layout-3">
                  /month
                </Text>
              </HStack>
            ) : (
              option.badge
            )}
          </HStack>
          <Text level="caption" className="text-left text-content-layout-3">
            {option.descriptions?.join(' • ')}
          </Text>
        </VStack>
      </HStack>
    </RadioGroup.Item>
  )
}
