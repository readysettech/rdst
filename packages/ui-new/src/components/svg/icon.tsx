import * as AccessibleIcon from '@radix-ui/react-accessible-icon'
import { tv, type VariantProps } from '@rs/tailwind-base'
import type { IconBulkName, IconStrokeName } from '@rs/ui-icons/icon-name'
import type { SVGProps } from 'react'

export type { IconBulkName, IconStrokeName } from '@rs/ui-icons/icon-name'

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

export type IconListType = IconStrokeName | IconBulkName

// The sprite a `variant` loads only contains that variant's symbols, so the
// `name` must come from the matching set. Tying them together makes a
// mismatched name (which silently renders a blank icon) a compile error.
type IconVariantProps =
  | { variant?: 'stroke'; name: IconStrokeName }
  | { variant: 'bulk'; name: IconBulkName }

export type IconProps = IconVariantProps & {
  /**
   * What the icon says that the surrounding markup does not. Pass `''` when the
   * icon sits beside its own visible label: the icon is then decorative and
   * contributes nothing to the accessible name, instead of announcing that
   * label a second time.
   */
  label: string
  className?: string
} & IconStyleProps &
  SVGProps<SVGSVGElement>

export const Icon = ({
  size,
  name,
  label,
  variant = 'stroke',
  className,
  ...rest
}: IconProps) => {
  const svg = (
    <svg
      {...rest}
      aria-hidden={label ? rest['aria-hidden'] : 'true'}
      focusable="false"
      className={iconRecipe({ size, class: className })}
      style={variant === 'bulk' ? { strokeWidth: 0 } : undefined}
    >
      <use href={`/icons/sprite-${variant}.svg#${name}`} />
    </svg>
  )

  // A decorative icon carries no name of its own: wrapping it would add a
  // screen-reader-only copy of the label the user can already see.
  if (!label) return svg

  return <AccessibleIcon.Root label={label}>{svg}</AccessibleIcon.Root>
}
