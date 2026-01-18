import { cn } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'
import { HStack } from './stack'
import { Text } from './text'

type DataDisplayProps = {
  label?: string
  value?: string
} & ComponentProps<'div'>

export const DataDisplay = ({
  label,
  value,
  children,
  className,
  ...rest
}: DataDisplayProps) => {
  return (
    <HStack className={cn('gap-1', className)} {...rest}>
      {label && (
        <Text level="body-small" className={cn('text-content-layout-3')}>
          {label}
        </Text>
      )}
      {children
        ? children
        : value && (
            <Text
              level="label-medium"
              className={cn(
                'overflow-hidden',
                'text-ellipsis',
                'whitespace-nowrap',
                'max-w-340px'
              )}
            >
              {value}
            </Text>
          )}
    </HStack>
  )
}
