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
  cleanUpThen(state, cleanup, quit)
}

/**
 * Runs the quit cleanup once, then `next`, after which windows close and a quit goes through.
 * Does nothing once a quit has started.
 */
export function cleanUpThen(
  state: { started: boolean; completed: boolean },
  cleanup: () => Promise<void>,
  next: () => void
): void {
  if (state.started) return
  state.started = true
  void cleanup().catch((error) => {
    console.warn('[rdst-desktop] cleanup failed:', error)
  }).finally(() => {
    state.completed = true
    next()
  })
}

/**
 * Starts an install that should quit the app. An install that can't proceed reports an error
 * or just returns, which would leave the app running without the background work its cleanup
 * stopped, so either one, or no quit within `timeoutMs`, ends the app.
 */
export function installOrQuit(options: {
  install: () => void
  onError: (listener: () => void) => void
  quit: () => void
  timeoutMs: number
}): void {
  let quitting = false
  const quit = () => {
    if (quitting) return
    quitting = true
    options.quit()
  }
  options.onError(quit)
  setTimeout(quit, options.timeoutMs).unref?.()
  try {
    options.install()
  } catch (error) {
    console.warn('[rdst-desktop] update install failed:', error)
    quit()
  }
}
