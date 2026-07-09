import { cn } from "@rs/tailwind-base";
import * as ScrollArea from "@rs/ui-new/scroll";

interface MainProps {
  children: React.ReactNode;
  isElectronMac?: boolean;
}

export function Main({ children, isElectronMac = false }: MainProps) {
  return (
    <ScrollArea.Root asChild>
      <main
        className={cn(
          "relative pl-64 h-[calc(100dvh-56px)] overflow-y-hidden",
          isElectronMac ? "bg-surface-layout-2/30" : "bg-surface-layout-2",
        )}
      >
        <ScrollArea.Viewport className="w-full h-full overflow-auto custom-scrollbar">
          <div className="p-6 max-w-6xl container">{children}</div>
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar
          className="absolute flex select-none touch-none bg-border-layout-2 transition-[background, width] duration-fast ease-base hover:w-4 data-orientation=vertical:w-2"
          orientation="vertical"
        >
          <ScrollArea.Thumb className="relative flex-1 bg-content-layout-disabled transition-[background] duration-fast ease-base hover:bg-content-layout-3" />
        </ScrollArea.Scrollbar>
        <ScrollArea.Corner />
      </main>
    </ScrollArea.Root>
  );
}
