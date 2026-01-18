import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { formatMoney } from '../../../../helpers/format'
import { HStack } from '../../../element/stack'
import { Tag } from '../../../element/tag'
import { Text } from '../../../element/text'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const clusterSize2Styles = tv({
  slots: {
    item: [
      'flex',
      'h-auto',
      'w-full',
      'flex-col',
      'items-start',
      'overflow-hidden',
      'rounded-2xl',
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
  info?: string
  specs?: {
    cache?: string
    disk?: string
    network?: string
  }
}

export const renderClusterSize2 = (option: CustomOptionsType) => {
  const styles = clusterSize2Styles()

  const isFreeTier =
    option.available === true &&
    (option.value === 'Free' || option.value === 'QueryPilotXSmall')

  return (
    <RadioGroup.Item
      value={option.value}
      className={styles.item()}
      disabled={option.disabled}
    >
      <div className="grid w-full grid-cols-11 items-start gap-3 p-3">
        <div className="col-span-4 flex items-center gap-3">
          <div className={styles.indicator()}>
            <RadioGroup.Indicator>
              <Icon name="tick" label="Selected" />
            </RadioGroup.Indicator>
          </div>
          <Text level="label-medium">{option.label}</Text>
        </div>
        <div className="col-span-2 flex items-center">
          {option.available === true ? (
            isFreeTier ? (
              option.badge
            ) : (
              <Tag
                label="Available"
                size="small"
                variant="positive"
                modifier="outline"
              />
            )
          ) : (
            <Tag
              label="Purchase"
              size="small"
              variant="rising"
              modifier="outline"
            />
          )}
        </div>
        <div className="col-span-1">
          <Text level="label-medium" className="text-left">
            {option.specs?.cache}
          </Text>
        </div>
        <div className="col-span-1">
          <Text level="label-medium" className="text-left">
            {option.specs?.disk}
          </Text>
        </div>
        <div className="col-span-1">
          <Text level="label-medium" className="text-left">
            {option.specs?.network}
          </Text>
        </div>
        <div className="col-span-2 flex items-center justify-end">
          {option.price ? (
            <HStack className="items-baseline gap-1">
              <Text level="mono-large">{formatMoney(option.price)}</Text>
              <Text level="mono-small" className="text-content-layout-3">
                /month
              </Text>
            </HStack>
          ) : isFreeTier ? (
            <HStack className="gap-1">
              <Text level="mono-large">Free</Text>
            </HStack>
          ) : (
            option.badge
          )}
        </div>
      </div>
      {option.info && (
        <HStack className="w-full items-start gap-2 border-t-(length:--border-base) border-border-layout-1 px-4 py-2 text-content-warning-plain">
          <Icon name="info" label="Information" />
          <Text level="caption" className="text-left text-content-layout-3">
            {option.info}
          </Text>
        </HStack>
      )}
    </RadioGroup.Item>
  )
}
