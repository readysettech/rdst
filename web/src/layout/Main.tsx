import { cn } from '@rs/tailwind-base'
import * as ScrollArea from '@rs/ui-new/scroll'

interface MainProps {
  children: React.ReactNode
  isElectronMac?: boolean
}

export function Main({ children, isElectronMac = false }: MainProps) {
  return (
    <ScrollArea.Root asChild>
      <main
        className={cn(
          // Sidebar offset only from tablet up; below that the sidebar is
          // off-canvas so content spans full width (responsive chrome, T19).
          'relative tablet:pl-64 h-[calc(100dvh-56px)] overflow-y-hidden',
          isElectronMac ? 'bg-surface-layout-2/30' : 'bg-surface-layout-2'
        )}
      >
        {/* `[&>div]:!block` overrides Radix's internal `display:table;
            min-width:100%` viewport wrapper. As a table it shrink-wraps to a
            child's max-width, so `max-w-6xl` grew it past the sidebar-narrowed
            viewport and the overflow-x-hidden viewport clipped feature CTAs at
            1280×720 (P21/P22/P23). As a block it stays 100% wide, so the fluid
            content column below fits and the CTAs stay reachable. */}
        <ScrollArea.Viewport className="w-full h-full overflow-auto custom-scrollbar [&>div]:!block">
          {/* Fluid content column: w-full + max-w + min-w-0, never the fixed
              `container` width (which computes off the viewport, ignoring the
              264px sidebar). Centered by mx-auto on wide screens. */}
          <div
            id="main-content"
            className="p-6 w-full max-w-6xl min-w-0 mx-auto"
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
