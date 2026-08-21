import { cn } from '@rs/tailwind-base'
import { IconButton } from '@rs/ui-new/icon-button'

/**
 * The star a query carries wherever it is shown: always visible, one click,
 * and reversible.
 *
 * A marked query reads as marked from across the list: the glyph itself fills
 * in (`star-filled`) and takes the primary content colour, so shape carries the
 * state alongside colour, and `aria-pressed` plus the label carry it for
 * assistive tech.
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
      icon={starred ? 'star-filled' : 'star'}
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
        starred ? 'text-content-primary-soft' : 'text-content-layout-3',
        className
      )}
    />
  )
}
