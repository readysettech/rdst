import * as RadioGroup from '@radix-ui/react-radio-group'
import { tv } from '@rs/tailwind-base'
import { formatMoney } from '../../../../helpers/format'
import { HStack } from '../../../element/stack'
import { Text } from '../../../element/text'
import { Icon } from '../../../svg/icon'
import type { OptionsType } from '../../base/input-radio-group'

const clusterSize3Styles = tv({
  slots: {
    item: [
      'flex',
      'flex-col',
      'w-full',
      'min-h-10',
      'bg-surface-layout-1',
      'transition-all',
      'duration-fast',
      'ease-base',
      'text-content-layout-3',
      'text-body-medium',
      'items-start',
      'cursor-pointer',
      'border-(length:--border-base)',
      'border-border-layout-1',
      'rounded-2xl',
      'overflow-hidden',

      'focus-visible:outline-none',
      'focus-visible:shadow-focus',
      'disabled:cursor-not-allowed',
      'disabled:opacity-50',

      'data-[state=checked]:bg-surface-layout-1',
      'data-[state=checked]:border-border-primary-solid',
      'data-[state=checked]:text-content-primary-soft',
    ],
    indicator: [
      'flex',
      'h-6',
      'w-6',
      'min-w-6',
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
  available?: number
  descriptions?: string[]
  info?: string
  specs?: {
    cache?: string
    disk?: string
    network?: string
  }
}

export const renderClusterSize3 = (option: CustomOptionsType) => {
  const styles = clusterSize3Styles()
  const isLocked = option.available === 0
  const isFreeTier =
    option.available !== 0 &&
    (option.value === 'Free' ||
      option.value === 'QueryPilotXSmall' ||
      option.value === 'Observability')

  return (
    <RadioGroup.Item
      value={option.value}
      className={styles.item()}
      disabled={option.disabled}
    >
      <div className="flex w-full flex-col items-start gap-3 p-6">
        <HStack className="w-full items-start justify-between">
          <HStack className="items-start gap-3">
            {isLocked ? (
              <Icon name="lock-sync" label="Locked" size="medium" />
            ) : (
              <div className={styles.indicator()}>
                <RadioGroup.Indicator>
                  <div className="flex h-6 w-6 items-center justify-center rounded-md bg-surface-primary-solid text-content-primary-solid">
                    <Icon name="tick" label="Selected" />
                  </div>
                </RadioGroup.Indicator>
              </div>
            )}
            <HStack className="w-full justify-between">
              <Text level="label-medium" className="text-content-layout-1">
                {option.label}
              </Text>
              {isFreeTier ? option.badge : null}
            </HStack>
          </HStack>
          <HStack className="justify-end text-content-layout-1">
            {option.available !== 0 &&
            option.value !== 'Free' &&
            option.value !== 'QueryPilotXSmall' &&
            option.value !== 'Observability' ? (
              <HStack className="gap-1 whitespace-nowrap">
                <Text level="mono-large">{option.available}</Text>
                <Text level="mono-small" className="text-content-layout-3">
                  /subscription available
                </Text>
              </HStack>
            ) : option.price ? (
              <HStack className="gap-1">
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
          </HStack>
        </HStack>
        {option.value !== 'Observability' && (
          <HStack className="w-full items-baseline gap-1 pl-8 text-content-layout-2">
            <Text level="caption" className="text-left">
              {option.specs?.cache} Cache
            </Text>
            <Text level="caption">•</Text>
            <Text level="caption" className="text-left">
              {option.specs?.disk} Storage
            </Text>
            <Text level="caption">•</Text>
            <Text level="caption" className="text-left">
              {option.specs?.network} Network
            </Text>
          </HStack>
        )}
      </div>
      {option.info && (
        <HStack className="w-full items-center gap-2 border-t-(length:--border-base) border-border-layout-1 px-4 py-2 text-content-warning-plain">
          <Icon
            name="info"
            label="Information"
            className="text-content-warning-soft"
          />
          <Text level="caption" className="text-left text-content-layout-3">
            {option.info}
          </Text>
        </HStack>
      )}
    </RadioGroup.Item>
  )
}
