import * as RadixProgress from '@radix-ui/react-progress'
import { tv } from '@rs/tailwind-base'

export const progressRecipe = tv({
  slots: {
    container: [
      'relative',
      'overflow-hidden',
      'bg-border-layout-1',
      'rounded-4',
      'w-60',
      'h-4',
    ],
    indicator: [
      'bg-content-rising-plain',
      'w-full',
      'h-full',
      'transition-all',
      'duration-slower',
      'ease-base',
    ],
  },
})

type ProgressProps = {
  value: number
  max: number
}

export const Progress = ({ value, max }: ProgressProps) => {
  const { container, indicator } = progressRecipe()
  return (
    <RadixProgress.Root className={container()} value={value} max={max}>
      <RadixProgress.Indicator
        className={indicator()}
        style={{ transform: `translateX(-${100 - value}%)` }}
      />
    </RadixProgress.Root>
  )
}
