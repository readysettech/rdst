import {
  type CnOptions,
  cn as cnDefault,
  createTV,
  type TVConfig,
  type TWMergeConfig,
} from 'tailwind-variants'

const twMergeConfig: TWMergeConfig = {
  extend: {
    classGroups: {
      'font-size': [
        'text-stat-hero',
        'text-display-large',
        'text-display-medium',
        'text-display-small',
        'text-headline-1',
        'text-headline-2',
        'text-headline-3',
        'text-headline-4',
        'text-headline-5',
        'text-subtitle-1',
        'text-subtitle-2',
        'text-button-large',
        'text-button-medium',
        'text-button-small',
        'text-label-large',
        'text-label-medium',
        'text-label-small',
        'text-label-extra-small',
        'text-body-large',
        'text-body-medium',
        'text-body-small',
        'text-caption',
        'text-overline',
        'text-mono-large',
        'text-mono-medium',
        'text-mono-small',
      ],
    },
  },
}

const tvConfig: TVConfig = {
  twMerge: true,
  twMergeConfig,
}

export const tv = createTV(tvConfig)

export const cn = <T extends CnOptions>(...classes: T) =>
  cnDefault(classes)(tvConfig)

export type {
  ClassProp,
  ClassValue,
  VariantProps,
} from 'tailwind-variants'
