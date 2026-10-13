import * as ScrollArea from '@radix-ui/react-scroll-area'
import { cn } from '@rs/tailwind-base'
import type { ComponentPropsWithoutRef, Ref } from 'react'
import type { WithChildren, WithClassName } from '../../helpers/types'

type ScrollableProps = {
  orientation?: 'vertical' | 'horizontal'
  /**
   * Radix ScrollArea visibility. Defaults to `hover` (scrollbar auto-hides).
   * Use `auto` for a persistent scrollbar whenever the content overflows, so
   * off-screen content is discoverable.
   */
  type?: 'auto' | 'always' | 'scroll' | 'hover'
  /**
   * Classes for the root box: its place in the parent layout (`min-h-0
   * flex-1` in a flex column) and any surface it draws (background, border,
   * radius). `className` styles the scrolling viewport inside it.
   */
  rootClassName?: string
  /** The scrolling element, for reading or resetting the scroll position. */
  viewportRef?: Ref<HTMLDivElement>
} & Omit<
  ComponentPropsWithoutRef<typeof ScrollArea.Viewport>,
  'asChild' | 'className' | 'children'
> &
  WithChildren &
  WithClassName

export const Scrollable = ({
  children,
  className,
  orientation = 'vertical',
  type = 'hover',
  rootClassName,
  viewportRef,
  ...viewportProps
}: ScrollableProps) => (
  <ScrollArea.Root
    type={type}
    // The root clips but is still scrollable programmatically: a
    // scrollIntoView scrolls every scrollable ancestor, which would offset
    // the viewport inside it. Pin it so only the viewport ever scrolls.
    onScroll={(event) => {
      const root = event.currentTarget
      if (root.scrollTop !== 0) root.scrollTop = 0
      if (root.scrollLeft !== 0) root.scrollLeft = 0
    }}
    className={cn(
      'transition-[padding]',
      'duration-fast',
      'ease-base',
      'h-[inherit]',
      'w-full',
      'overflow-hidden',
      // A column, so the viewport fills a root sized by its parent's flex
      // layout even when no ancestor has a definite height (a dialog body).
      'flex',
      'flex-col',
      rootClassName
    )}
  >
    {/* Radix ScrollArea's viewport sets display:table, which ignores
        descendants' min-w-0; force block so vertical scrollables can wrap and
        inner overflow-x containers can scroll instead of clipping. */}
    <ScrollArea.Viewport
      ref={viewportRef}
      {...viewportProps}
      className={cn(
        'w-full',
        'h-full',
        'min-h-0',
        'flex-1',
        // Radix hides the native scrollbar with an injected style; these keep it
        // hidden where that style does not apply.
        '[scrollbar-width:none]',
        '[&::-webkit-scrollbar]:hidden',
        orientation === 'vertical' && '[&>div]:!block',
        className
      )}
    >
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
