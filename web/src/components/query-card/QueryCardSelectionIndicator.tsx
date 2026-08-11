import { Icon } from '@rs/ui-new/icon'

export function QueryCardSelectionIndicator({
  selected,
}: {
  selected: boolean
}) {
  return (
    <div
      aria-hidden="true"
      className={
        selected
          ? 'flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2 border-surface-primary-solid bg-surface-primary-solid transition-colors'
          : 'flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2 border-border-layout-2 transition-colors'
      }
    >
      {selected ? (
        <Icon
          name="tick"
          label=""
          aria-hidden="true"
          className="h-3 w-3 text-content-primary-solid"
        />
      ) : null}
    </div>
  )
}
