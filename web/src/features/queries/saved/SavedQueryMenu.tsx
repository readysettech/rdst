import { Dropdown } from '@rs/ui-new/dropdown'
import { IconButton } from '@rs/ui-new/icon-button'

interface SavedQueryMenuProps {
  onEditSql: () => void
  onRename: () => void
  onMarkReviewed?: () => void
  /** Present only once a stored analysis exists, since View then leads the row. */
  onReRunAnalysis?: () => void
  onDelete: () => void
}

export function SavedQueryMenu({
  onEditSql,
  onRename,
  onMarkReviewed,
  onReRunAnalysis,
  onDelete,
}: SavedQueryMenuProps) {
  return (
    <Dropdown>
      <Dropdown.Trigger asChild>
        <IconButton
          variant="primary"
          modifier="ghost"
          size="small"
          icon="more"
          label="More actions"
        />
      </Dropdown.Trigger>
      <Dropdown.Content align="end" className="min-w-52">
        {onReRunAnalysis ? (
          <Dropdown.Item
            leftIcon="play"
            label="Re-run analysis"
            onSelect={onReRunAnalysis}
          />
        ) : null}
        <Dropdown.Item
          leftIcon="filter-edit"
          label="Edit SQL"
          onSelect={onEditSql}
        />
        <Dropdown.Item leftIcon="edit" label="Rename" onSelect={onRename} />
        {onMarkReviewed ? (
          <Dropdown.Item
            leftIcon="tick"
            label="Mark reviewed"
            onSelect={onMarkReviewed}
          />
        ) : null}
        <Dropdown.Separator />
        <Dropdown.Item
          leftIcon="trash"
          label="Delete"
          className="text-content-negative-soft hover:text-content-negative-soft focus:text-content-negative-soft hover:bg-surface-negative-soft focus:bg-surface-negative-soft"
          onSelect={onDelete}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}
