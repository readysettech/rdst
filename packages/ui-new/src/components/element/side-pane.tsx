import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { Scrollable } from '../overlay/scrollable'
import { IconButton } from './icon-button'

export interface SidePaneProps {
  /** Names the pane for assistive technology (the open item's title). */
  'aria-label': string
  /** The state chip at the start of the header. */
  chip?: ReactNode
  /** The header's third-tone facts beside the chip. */
  meta?: ReactNode
  onPrev?: () => void
  onNext?: () => void
  onClose: () => void
  /** Actions under the body, above the panel's bottom edge. */
  footer?: ReactNode
  children: ReactNode
  className?: string
}

/**
 * The second panel of a TwoPane: a 60px header with the chip, the meta and
 * previous / next / close, then a body that scrolls on its own.
 */
export const SidePane = ({
  'aria-label': ariaLabel,
  chip,
  meta,
  onPrev,
  onNext,
  onClose,
  footer,
  children,
  className,
}: SidePaneProps) => (
  <section
    aria-label={ariaLabel}
    className={cn('flex h-full min-h-0 flex-col', className)}
  >
    <div className="flex h-15 shrink-0 items-center gap-3 border-b border-border-layout-2 pr-4 pl-6 [-webkit-app-region:drag]">
      {chip}
      {meta && (
        <div className="flex items-center gap-2.5 text-caption text-content-layout-3">
          {meta}
        </div>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-0.5 [-webkit-app-region:no-drag]">
        {onPrev && (
          <IconButton
            icon="chevron-up"
            label="Previous"
            variant="primary"
            modifier="ghost"
            size="small"
            tooltip={false}
            onClick={onPrev}
          />
        )}
        {onNext && (
          <IconButton
            icon="chevron-down"
            label="Next"
            variant="primary"
            modifier="ghost"
            size="small"
            tooltip={false}
            onClick={onNext}
          />
        )}
        <IconButton
          icon="close"
          label="Close"
          variant="primary"
          modifier="ghost"
          size="small"
          tooltip={false}
          onClick={onClose}
        />
      </div>
    </div>
    <Scrollable rootClassName="min-h-0 flex-1" type="auto">
      <div className="px-6 pt-5 pb-6">{children}</div>
    </Scrollable>
    {footer && (
      <div className="flex shrink-0 gap-1.5 border-t border-border-layout-2 px-6 py-3.5">
        {footer}
      </div>
    )}
  </section>
)
