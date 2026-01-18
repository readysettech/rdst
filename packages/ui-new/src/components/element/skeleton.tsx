import { cn } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'

export const Skeleton = ({ className, ...props }: ComponentProps<'div'>) => (
  <div
    id="skeleton"
    className={cn(
      ['animate-pulse', 's-2', 'bg-surface-layout-disabled'],
      className
    )}
    {...props}
  />
)
