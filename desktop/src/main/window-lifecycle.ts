import type { BrowserWindow } from 'electron'

export function hideWindowOnClose(window: BrowserWindow, isQuitting: () => boolean): void {
  window.on('close', (event) => {
    if (isQuitting()) return
    event.preventDefault()
    window.hide()
  })
}

export function stopBeforeQuit(
  event: { preventDefault: () => void },
  state: { started: boolean; completed: boolean },
  cleanup: () => Promise<void>,
  quit: () => void
): void {
  if (state.completed) return
  event.preventDefault()
  if (state.started) return
  state.started = true
  void cleanup().catch((error) => {
    console.warn('[rdst-desktop] cleanup failed:', error)
  }).finally(() => {
    state.completed = true
    quit()
  })
}
