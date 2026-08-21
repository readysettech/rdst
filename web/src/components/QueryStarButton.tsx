import { cn } from '@rs/tailwind-base'
import { IconButton } from '@rs/ui-new/icon-button'

/**
 * The star a query carries wherever it is shown: always visible, one click,
 * and reversible.
 *
 * `@rs/ui-icons` ships one stroke star and no filled variant, and the sprite
 * symbol sets `fill="none"` on itself, so a filled glyph is not reachable from
 * the outside. The on-state therefore uses the design system's established
 * active-toggle treatment — a soft filled chip behind the glyph — plus
 * `aria-pressed`, so the mark never rests on colour alone.
 */
export function QueryStarButton({
  starred,
  onToggle,
  className,
}: {
  starred: boolean
  onToggle: (starred: boolean) => void
  className?: string
}) {
  return (
    <IconButton
      icon="star"
      size="small"
      variant="primary"
      modifier="ghost"
      label={starred ? 'Starred' : 'Star this query'}
      aria-pressed={starred}
      data-testid="query-star-toggle"
      onClick={(event) => {
        // The star sits inside cards and rows that are themselves clickable.
        event.stopPropagation()
        onToggle(!starred)
      }}
      className={cn(
        starred
          ? 'bg-surface-primary-soft text-content-primary-soft'
          : 'text-content-layout-3',
        className
      )}
    />
  )
}
