import * as ScrollArea from '@radix-ui/react-scroll-area'
import { cn } from '@rs/tailwind-base'
import type { WithChildren, WithClassName } from '../../helpers/types'

type ScrollableProps = {
  orientation?: 'vertical' | 'horizontal'
} & WithChildren &
  WithClassName

export const Scrollable = ({
  children,
  className,
  orientation = 'vertical',
}: ScrollableProps) => (
  <ScrollArea.Root
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
        'data-orientation=vertical:w-2',
        'data-orientation=vertical:hover:w-4',
        'data-orientation=horizontal:h-2',
        'data-orientation=horizontal:hover:h-4',
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
