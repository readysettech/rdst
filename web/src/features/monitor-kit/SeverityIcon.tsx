import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import type { InboxTone } from '@/lib/monitor/types'
import { alertTypeIcon, TONE_ICON_CLASS } from './severity'

export interface SeverityIconProps {
  /** Alert type; picks the glyph. */
  type: string
  tone: InboxTone
  size?: 'small' | 'base'
  /** Accessible label; empty when a visible title says the same thing. */
  label?: string
  className?: string
}

/**
 * Tone-colored glyph tile for an inbox alert. The glyph carries the alert type,
 * the tile color carries the state, so status never rests on color alone.
 */
export function SeverityIcon({
  type,
  tone,
  size = 'base',
  label = '',
  className,
}: SeverityIconProps) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-panel',
        size === 'base' ? 'h-9 w-9' : 'h-7 w-7',
        TONE_ICON_CLASS[tone],
        className
      )}
    >
      <Icon
        name={alertTypeIcon(type, tone)}
        label={label}
        className={size === 'base' ? 'h-4 w-4' : 'h-3.5 w-3.5'}
      />
    </span>
  )
}
