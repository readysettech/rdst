import { cn } from '@rs/tailwind-base'
import {
  type ComponentPropsWithoutRef,
  type CSSProperties,
  forwardRef,
} from 'react'

export type DividerProps = ComponentPropsWithoutRef<'div'> & {
  orientation?: 'horizontal' | 'vertical'
  thickness?: number | string
}

type CSSPropertiesWithVars = CSSProperties & Record<string, string | number>

export const Divider = forwardRef<HTMLDivElement, DividerProps>(
  (
    {
      orientation = 'horizontal',
      thickness = '1.5px',
      className,
      role = 'separator',
      style,
      ...rest
    },
    ref
  ) => {
    const isVertical = orientation === 'vertical'
    const thicknessValue =
      typeof thickness === 'number' ? `${thickness}px` : thickness
    const orientationProps = isVertical
      ? ({ 'aria-orientation': 'vertical' } as const)
      : undefined

    const inlineStyle = {
      ...style,
      '--divider-thickness': thicknessValue,
    } as CSSPropertiesWithVars

    return (
      <div
        ref={ref}
        role={role}
        className={cn(
          'bg-border-layout-soft',
          isVertical
            ? 'h-full w-(--divider-thickness)'
            : 'h-(--divider-thickness) w-full',
          className
        )}
        style={inlineStyle}
        {...orientationProps}
        {...rest}
      />
    )
  }
)

Divider.displayName = 'Divider'
