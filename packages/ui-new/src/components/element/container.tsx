import { tv, type VariantProps } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'

const containerStyles = tv({
  base: 'relative mx-auto container px-4 tablet:px-6 laptop:px-8',
})

export type ContainerProps = ComponentProps<'div'> &
  VariantProps<typeof containerStyles>

export const Container = ({ className, ...props }: ContainerProps) => {
  return <div className={containerStyles({ className })} {...props} />
}
