import { tv, type VariantProps } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'

// The content axis every page shares: a centered column with 48px side padding,
// so the page bar's title and the first row of content start on the same line.
export const pageContainerStyles = tv({
  base: 'mx-auto w-full min-w-0 px-12',
  variants: {
    width: {
      /** 1120px of content, the default for every page. */
      content: 'max-w-[1216px]',
      /** 860px of content for reading pages, centered on the same axis. */
      reading: 'max-w-[956px]',
    },
  },
  defaultVariants: { width: 'content' },
})

export type PageContainerProps = ComponentProps<'div'> &
  VariantProps<typeof pageContainerStyles>

export const PageContainer = ({
  className,
  width,
  ...props
}: PageContainerProps) => (
  <div className={pageContainerStyles({ width, className })} {...props} />
)
