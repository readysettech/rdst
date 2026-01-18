import * as AccessibleIcon from '@radix-ui/react-accessible-icon'
import { tv, type VariantProps } from '@rs/tailwind-base'
import type { IconName } from '@rs/ui-icons/icon-name'
import type { SVGProps } from 'react'

export const iconRecipe = tv({
  base: ['inline', 'justify-self-center', 'stroke-current'],
  variants: {
    size: {
      small: ['h-4', 'w-4', 'min-w-4', 'stroke-2'],
      base: ['h-5', 'w-5', 'min-w-5', 'stroke-[1.5px]'],
      medium: ['h-6', 'w-6', 'min-w-6', 'stroke-[1.5px]'],
      large: ['h-8', 'w-8', 'min-w-8', 'stroke-[1.25px]'],
      xlarge: ['h-10', 'w-10', 'min-w-10', 'stroke-[1.25px]'],
      xxlarge: ['h-20', 'w-20', 'min-w-20', 'stroke-1'],
    },
  },
  defaultVariants: {
    size: 'base',
  },
})
type IconStyleProps = VariantProps<typeof iconRecipe>

export type IconListType = IconName

export type IconProps = {
  name: IconListType
  label: string
  className?: string
} & IconStyleProps &
  SVGProps<SVGSVGElement>

export const Icon = ({ size, name, label, className, ...rest }: IconProps) => {
  return (
    <AccessibleIcon.Root label={label}>
      <svg {...rest} className={iconRecipe({ size, class: className })}>
        <use href={`/icons/sprite.svg#${name}`} />
      </svg>
    </AccessibleIcon.Root>
  )
}
