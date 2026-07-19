import * as ScrollArea from '@radix-ui/react-scroll-area'
import { cn } from '@rs/tailwind-base'
import type { WithChildren, WithClassName } from '../../helpers/types'

type ScrollableProps = {
  orientation?: 'vertical' | 'horizontal'
  /**
   * Radix ScrollArea visibility. Defaults to `hover` (scrollbar auto-hides).
   * Use `auto` for a persistent scrollbar whenever the content overflows, so
   * off-screen content is discoverable.
   */
  type?: 'auto' | 'always' | 'scroll' | 'hover'
} & WithChildren &
  WithClassName

export const Scrollable = ({
  children,
  className,
  orientation = 'vertical',
  type = 'hover',
}: ScrollableProps) => (
  <ScrollArea.Root
    type={type}
    className={cn([
      'transition-[padding]',
      'duration-fast',
      'ease-base',
      'h-[inherit]',
      'w-full',
      'overflow-hidden',
    ])}
  >
    <ScrollArea.Viewport className={cn('w-full', 'h-full', className)}>
      {children}
    </ScrollArea.Viewport>
    <ScrollArea.Scrollbar
      className={cn([
        'flex',
        'select-none',
        'touch-none',
        'bg-border-layout-2',
        'transition-[background,width]',
        'duration-fast',
        'ease-base',
        // Correct Tailwind v4 data-variant syntax — the bracketless form
        // generated no rule, so the scrollbar rendered at 0px width and was
        // never visible (overlay scrollbar, so no layout shift). [QW2]
        'data-[orientation=vertical]:w-2',
        'data-[orientation=vertical]:hover:w-4',
        'data-[orientation=horizontal]:h-2',
        'data-[orientation=horizontal]:hover:h-4',
      ])}
      orientation={orientation}
    >
      <ScrollArea.Thumb
        className={cn([
          'relative',
          'flex-1',
          'bg-content-layout-disabled',
          'transition-[background]',
          'duration-fast',
          'ease-base',
          'hover:bg-content-layout-3',
        ])}
      />
    </ScrollArea.Scrollbar>
  </ScrollArea.Root>
)
