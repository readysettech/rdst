import * as RadixLabel from '@radix-ui/react-label'
import { tv } from '@rs/tailwind-base'
import { type ComponentRef, forwardRef, memo } from 'react'

export type LabelProps = RadixLabel.LabelProps & {
  required?: boolean
}

const labelStyles = tv({
  base: [
    'inline-flex',
    'items-center',
    'text-label-medium',
    'text-content-layout-1',
    '[&_span]:text-content-negative-plain',
    '[&_span]:ml-1',
  ],
})

const Label = forwardRef<ComponentRef<typeof RadixLabel.Root>, LabelProps>(
  ({ children, required, className, ...props }, ref) => (
    <RadixLabel.Root
      ref={ref}
      {...props}
      className={labelStyles({ class: className })}
    >
      {children}
      {required && <span>*</span>}
    </RadixLabel.Root>
  )
)

Label.displayName = 'Label'

const MemoizedLabel = memo(Label)

export { MemoizedLabel as Label, labelStyles }
