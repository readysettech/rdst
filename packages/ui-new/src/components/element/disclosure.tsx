'use client'

import { cn } from '@rs/tailwind-base'
import { AnimatePresence, m } from 'motion/react'
import { memo, type ReactNode, useId, useRef } from 'react'
import { useDisclosure } from '../../hooks/use-disclosure'
import { Icon } from '../svg/icon'
import { VStack } from './stack'
import { Text } from './text'

interface DisclosureProps {
  /** The always-visible summary. Ignored when `trigger` is supplied. */
  title?: ReactNode
  /** Secondary line under the title (a short hint or current value). */
  subtitle?: ReactNode
  /**
   * Full replacement for the default title/subtitle layout. The chevron is
   * still rendered; supply the leading content only.
   */
  trigger?: ReactNode
  /** The collapsible panel content. */
  children: ReactNode
  /** Initial open state for the uncontrolled component. */
  defaultOpen?: boolean
  /** Controlled open state. Pair with `onOpenChange`. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Disable the trigger; the panel keeps its current open state. */
  disabled?: boolean
  className?: string
  /** Class applied to the inner padded panel wrapper. */
  panelClassName?: string
}

/**
 * A single collapsible section: a real `<button aria-expanded aria-controls>`
 * paired with a panel that shares the button's `aria-controls` id. Wraps the
 * shared {@link useDisclosure} hook (controlled + uncontrolled) and the motion
 * height animation used across the app's hand-rolled disclosures.
 *
 * The panel unmounts when closed, so it is for supplementary content only —
 * never wrap required or critical inputs whose state must survive a collapse.
 * See the usage doc for the Carbon-derived rules (user-initiated only, no
 * nesting, never hide critical content).
 */
function DisclosureImpl({
  title,
  subtitle,
  trigger,
  children,
  defaultOpen,
  open: controlledOpen,
  onOpenChange,
  disabled,
  className,
  panelClassName,
}: DisclosureProps) {
  const [open, setOpen] = useDisclosure({
    open: controlledOpen,
    onOpenChange,
  })
  const panelId = useId()

  // Seed the uncontrolled initial state once, at render time (no post-mount
  // flash). Controlled instances take their state from the `open` prop.
  const seeded = useRef(false)
  if (!seeded.current) {
    seeded.current = true
    if (controlledOpen === undefined && defaultOpen) setOpen(true)
  }

  return (
    <div
      className={cn(
        'rounded-xl border border-border-layout-1 overflow-hidden',
        className
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className={cn(
          'w-full flex items-center justify-between gap-3 px-4 py-3 text-left',
          'transition-colors',
          disabled
            ? 'cursor-not-allowed opacity-50'
            : 'cursor-pointer hover:bg-surface-layout-2/50'
        )}
      >
        {trigger ?? (
          <VStack className="gap-0.5 items-start min-w-0">
            {title && (
              <Text
                as="span"
                level="label-small"
                className="text-content-layout-1"
              >
                {title}
              </Text>
            )}
            {subtitle && (
              <Text
                as="span"
                level="caption"
                className="text-content-layout-3 truncate"
              >
                {subtitle}
              </Text>
            )}
          </VStack>
        )}
        <Icon
          name="chevron-down"
          label=""
          aria-hidden="true"
          className={cn(
            'w-4 h-4 text-content-layout-3 shrink-0 transition-transform duration-fast',
            open ? 'rotate-0' : '-rotate-90'
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <m.div
            id={panelId}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-layout-1"
          >
            <div className={cn('px-4 py-3', panelClassName)}>{children}</div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export const Disclosure = memo(DisclosureImpl)
export type { DisclosureProps }
