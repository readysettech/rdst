import * as ScrollArea from '@rs/ui-new/scroll'

interface MainProps {
  children: React.ReactNode
}

export function Main({ children }: MainProps) {
  return (
    <ScrollArea.Root asChild>
      <main
        // The ScrollArea root is `overflow-y-hidden`, but hidden containers are
        // still scrollable programmatically: an anchor/hash jump (or any
        // scrollIntoView) scrolls EVERY scrollable ancestor, so the root ends up
        // offset and drags the scroll viewport out of view. The user can never
        // scroll it back — the page looks frozen with dead space until a reload.
        // Pin the root at the origin so only the viewport ever scrolls.
        onScroll={(event) => {
          const root = event.currentTarget
          if (root.scrollTop !== 0) root.scrollTop = 0
          if (root.scrollLeft !== 0) root.scrollLeft = 0
        }}
        // Sidebar offset only from tablet up; below that the sidebar is
        // off-canvas so content spans full width (responsive chrome, T19).
        className="relative tablet:pl-80 h-[calc(100dvh-56px)] overflow-y-hidden bg-surface-layout-2"
      >
        {/* Radix's direct child is a `display:table; min-width:100%`
            measurement wrapper. Keep it as a table so its height tracks
            dynamically expanding report content, but pin it to the viewport
            with fixed table layout. That prevents wide descendants from
            shrink-wrapping it past the sidebar-narrowed viewport and clipping
            feature CTAs at 1280×720 (P21/P22/P23). */}
        <ScrollArea.Viewport className="w-full h-full overflow-auto custom-scrollbar [&>div]:!w-full [&>div]:!table-fixed">
          {/* Fluid content column: w-full + max-w + min-w-0, never the fixed
              `container` width (which computes off the viewport, ignoring the
              320px sidebar). Centered by mx-auto on wide screens. */}
          <div
            id="main-content"
            className="p-6 w-full max-w-7xl min-w-0 mx-auto"
          >
            {children}
          </div>
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar
          className="absolute flex select-none touch-none bg-border-layout-2 transition-[background,width] duration-fast ease-base w-2 hover:w-4 data-[orientation=vertical]:w-2 data-[orientation=vertical]:hover:w-4"
          orientation="vertical"
        >
          <ScrollArea.Thumb className="relative flex-1 bg-content-layout-disabled transition-[background] duration-fast ease-base hover:bg-content-layout-3" />
        </ScrollArea.Scrollbar>
        <ScrollArea.Corner />
      </main>
    </ScrollArea.Root>
  )
}
