import { createElement, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// The settings-page card shell every provider connection renders into, so the
// cards share a baseline in the Connections grid. Each card reads header, one
// line of context, then an action pinned to the bottom.
export const COMPACT_CARD =
  'flex flex-col h-full min-h-32 gap-3 rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4'

/** Provider details run long; keep an inline notice to a readable length. */
export function shorten(detail: string, limit: number): string {
  const text = detail.trim()
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}...` : text
}

/**
 * A connection panel's buttons: in the host dialog's footer when it offers one, in a row
 * at the end of the panel otherwise.
 */
export function panelActions(
  footer: HTMLElement | null | undefined,
  buttons: ReactNode
): ReactNode {
  return footer
    ? createPortal(buttons, footer)
    : createElement('div', { className: 'flex items-center gap-2' }, buttons)
}
