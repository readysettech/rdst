export interface DesktopWindowControls {
  minimize: () => void;
  toggleMaximize: () => void;
  close: () => void;
  isMaximized: () => Promise<boolean>;
  onMaximizedChange: (callback: (maximized: boolean) => void) => () => void;
}

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true;
      platform: string;
      windowControls?: DesktopWindowControls;
    };
  }
}

/**
 * True when running inside the rdst-desktop Electron shell on macOS, where the
 * window is transparent and native liquid glass shows through the layout.
 */
export function isDesktopMac(): boolean {
  return (
    window.rdstDesktop?.isDesktop === true &&
    window.rdstDesktop.platform === "darwin"
  );
}

/**
 * True when running inside the rdst-desktop Electron shell on Linux, where the
 * window is frameless and the renderer draws its own window controls.
 */
export function isDesktopLinux(): boolean {
  return (
    window.rdstDesktop?.isDesktop === true &&
    window.rdstDesktop.platform === "linux"
  );
}

export function getWindowControls(): DesktopWindowControls | undefined {
  return window.rdstDesktop?.windowControls;
}
