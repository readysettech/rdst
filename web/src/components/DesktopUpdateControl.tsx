import { Button } from '@rs/ui-new/button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Progress } from '@rs/ui-new/progress'
import { Text } from '@rs/ui-new/text'
import { useEffect, useRef, useState } from 'react'

import type { DesktopUpdateState } from '../lib/desktop'

function updateCopy(state: DesktopUpdateState): {
  button: string
  title: string
  detail: string
} {
  if (state.status === 'ready') {
    return {
      button: 'Restart',
      title: `Version ${state.version} is ready`,
      detail: 'Restart RDST to finish installing the update.',
    }
  }

  if (state.status === 'downloading') {
    const progress = Math.round(state.progress ?? 0)
    return {
      button: `${progress}%`,
      title: `Downloading version ${state.version}`,
      detail: 'RDST will be ready to restart when the download finishes.',
    }
  }

  return {
    button: 'Update',
    title: `Version ${state.version} is available`,
    detail:
      state.downloadLinks.length > 0
        ? 'Choose the download for your installation.'
        : 'The update will start downloading automatically.',
  }
}

export function DesktopUpdateControl({
  state,
  install,
}: {
  state: DesktopUpdateState | null
  install: () => void
}) {
  const [open, setOpen] = useState(false)
  const openTimer = useRef<number | null>(null)
  const closeTimer = useRef<number | null>(null)

  const clearTimers = () => {
    if (openTimer.current) window.clearTimeout(openTimer.current)
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
    openTimer.current = null
    closeTimer.current = null
  }
  useEffect(() => clearTimers, [])

  if (!state) return null

  const copy = updateCopy(state)
  const progress = Math.round(state.progress ?? 0)
  const hoverOpen = () => {
    clearTimers()
    openTimer.current = window.setTimeout(() => setOpen(true), 120)
  }
  const hoverClose = () => {
    clearTimers()
    closeTimer.current = window.setTimeout(() => setOpen(false), 220)
  }
  const handleClick = () => {
    if (state.status === 'ready') install()
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {/* Kept as a hand-roll: this is the PopoverTrigger asChild target with
            hover-intent handlers, and a compact h-7 status pill below the
            smallest Button/IconButton size — a Button here would grow its visual
            weight and IconButton's tooltip wrapper breaks asChild ref
            forwarding. */}
        <Pressable
          type="button"
          aria-label={`${copy.button}: ${copy.title}`}
          onClick={handleClick}
          onMouseEnter={hoverOpen}
          onMouseLeave={hoverClose}
          className="inline-flex h-7 min-w-14 cursor-pointer items-center justify-center rounded-lg border border-border-primary-soft bg-surface-primary-soft px-2 text-button-small text-content-primary-soft transition-colors hover:bg-surface-primary-soft-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft"
        >
          {copy.button}
        </Pressable>
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="end"
        sideOffset={8}
        className="w-72 flex-col gap-3 p-4"
        onMouseEnter={hoverOpen}
        onMouseLeave={hoverClose}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <div className="space-y-1">
          <Text as="p" level="label-small" className="text-content-layout-1">
            {copy.title}
          </Text>
          <Text as="p" level="caption" className="text-content-layout-3">
            {copy.detail}
          </Text>
        </div>

        {state.status === 'downloading' && (
          <div className="space-y-1.5">
            <Progress value={progress} max={100} />
            <Text as="p" level="caption" className="text-content-layout-2">
              {progress}% downloaded
            </Text>
          </div>
        )}

        {state.status === 'available' && state.downloadLinks.length > 0 && (
          <div className="flex gap-2">
            {state.downloadLinks.map((link) => (
              <a
                key={link.url}
                href={link.url}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border border-border-primary-soft px-3 py-1.5 text-button-small text-content-primary-soft transition-colors hover:bg-surface-primary-soft-hover"
              >
                Download {link.label}
              </a>
            ))}
          </div>
        )}

        {state.status === 'ready' && (
          <Button
            size="small"
            label="Restart to update"
            onClick={install}
            fullWidth
          />
        )}
      </PopoverContent>
    </Popover>
  )
}
