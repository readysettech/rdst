import type { ResultTone } from './resultsSelectors'

export const resultToneStyles: Record<
  ResultTone,
  {
    border: string
    text: string
    tag: 'positive' | 'informative' | 'warning' | 'negative'
    icon: 'tick-double' | 'info' | 'alert' | 'close'
  }
> = {
  positive: {
    border: 'border-border-positive-soft',
    text: 'text-content-positive-soft',
    tag: 'positive',
    icon: 'tick-double',
  },
  informative: {
    border: 'border-border-info-soft',
    text: 'text-content-info-soft',
    tag: 'informative',
    icon: 'info',
  },
  warning: {
    border: 'border-border-warning-soft',
    text: 'text-content-warning-soft',
    tag: 'warning',
    icon: 'alert',
  },
  negative: {
    border: 'border-border-negative-soft',
    text: 'text-content-negative-soft',
    tag: 'negative',
    icon: 'close',
  },
}
