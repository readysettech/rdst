export {}

declare global {
  interface Window {
    rdstDesktop?: {
      isDesktop: true
      platform: NodeJS.Platform
      windowControls: {
        minimize: () => void
        toggleMaximize: () => void
        close: () => void
        isMaximized: () => Promise<boolean>
        onMaximizedChange: (
          callback: (maximized: boolean) => void
        ) => () => void
      }
    }
  }
}
