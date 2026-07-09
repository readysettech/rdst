import { useEffect, useState } from "react";
import { cn } from "@rs/tailwind-base";
import { HStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { getWindowControls } from "../lib/desktop";

interface ControlButtonProps {
  label: string;
  onClick: () => void;
  negative?: boolean;
  children: React.ReactNode;
}

function ControlButton({ label, onClick, negative = false, children }: ControlButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "no-drag flex h-7 w-9 cursor-pointer items-center justify-center rounded-md",
        "text-content-layout-3 transition-colors",
        negative
          ? "hover:bg-surface-negative-solid hover:text-content-negative-solid"
          : "hover:bg-surface-layout-2 hover:text-content-layout-1",
      )}
    >
      {children}
    </button>
  );
}

function Glyph({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 10 10"
      className="h-2.5 w-2.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.1"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/**
 * Minimize / maximize / close cluster for the frameless Linux desktop shell.
 * Renders nothing when the preload bridge is absent (browser, macOS shell).
 */
export function WindowControls() {
  const controls = getWindowControls();
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!controls) return;
    let cancelled = false;
    void controls.isMaximized().then((value) => {
      if (!cancelled) setMaximized(value);
    });
    const unsubscribe = controls.onMaximizedChange(setMaximized);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [controls]);

  if (!controls) return null;

  return (
    <HStack className="no-drag items-center gap-1">
      <ControlButton label="Minimize" onClick={() => controls.minimize()}>
        <Glyph>
          <line x1="0.5" y1="5" x2="9.5" y2="5" />
        </Glyph>
      </ControlButton>
      <ControlButton
        label={maximized ? "Restore" : "Maximize"}
        onClick={() => controls.toggleMaximize()}
      >
        <Show
          when={maximized}
          fallback={
            <Glyph>
              <rect x="0.5" y="0.5" width="9" height="9" rx="1" />
            </Glyph>
          }
        >
          <Glyph>
            <rect x="0.5" y="2.5" width="7" height="7" rx="1" />
            <path d="M2.5 2.5V1.5a1 1 0 0 1 1-1h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-1" />
          </Glyph>
        </Show>
      </ControlButton>
      <ControlButton label="Close" onClick={() => controls.close()} negative>
        <Glyph>
          <line x1="1" y1="1" x2="9" y2="9" />
          <line x1="9" y1="1" x2="1" y2="9" />
        </Glyph>
      </ControlButton>
    </HStack>
  );
}
