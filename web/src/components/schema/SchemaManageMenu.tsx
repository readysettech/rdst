import {
  Dropdown,
  DropdownTrigger,
  DropdownContent,
  DropdownItem,
  DropdownSeparator,
  DropdownSub,
  DropdownSubTrigger,
  DropdownSubContent,
} from '@rs/ui-new/dropdown-rf'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack } from '@rs/ui-new/stack'

interface SchemaManageMenuProps {
  onRefresh: () => void
  onProfile: () => void
  onExport: (format: 'yaml' | 'json') => void
  onReinit: () => void
  onDelete: () => void
  /** Disable the whole menu while any schema operation is in flight (kills the
   *  concurrent-write race — audit LOW) or the target is password-locked. */
  disabled?: boolean
}

// DropdownSubTrigger is the unstyled Radix primitive; give it the same visual
// vocabulary the styled DropdownItem uses so the submenu row reads as a peer.
const subTriggerClass = [
  'relative flex h-10 min-w-60 items-center justify-between gap-2 px-4 py-1',
  'rounded-xl text-label-large text-content-layout-2 cursor-pointer select-none outline-none',
  'transition-colors duration-fast ease-base',
  'hover:bg-surface-primary-soft-hover hover:text-content-primary-soft',
  'focus:bg-surface-primary-soft-hover focus:text-content-primary-soft',
  'data-[state=open]:bg-surface-primary-soft-hover data-[state=open]:text-content-primary-soft',
].join(' ')

/**
 * Region A — the "Manage (⋯)" overflow menu (redesign §Remove/merge/defer). The
 * four header utilities (Refresh, Profile, Export, Re-init) plus the former
 * Danger-Zone Delete collapse behind one overflow trigger, leaving the header
 * with a single primary action. Export is a system submenu (Escape-dismiss +
 * correct anchoring, replacing the bespoke popover). The two destructive items
 * route through their own ConfirmDialogs, where the red lives.
 */
export function SchemaManageMenu({
  onRefresh,
  onProfile,
  onExport,
  onReinit,
  onDelete,
  disabled,
}: SchemaManageMenuProps) {
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          icon="more"
          iconPosition="icon"
          label="Manage"
          aria-label="Manage semantic layer"
          disabled={disabled}
        />
      </DropdownTrigger>
      <DropdownContent align="end" className="min-w-64">
        <DropdownItem leftIcon="database-settings" onClick={onRefresh}>
          Refresh structure
        </DropdownItem>
        <DropdownItem leftIcon="speedometer" onClick={onProfile}>
          Recompute statistics
        </DropdownItem>
        <DropdownSub>
          <DropdownSubTrigger className={subTriggerClass}>
            <HStack className="gap-2 items-center">
              <Icon name="folder-file" label="Export" />
              Export
            </HStack>
            <Icon name="chevron-right" label="Export options" />
          </DropdownSubTrigger>
          <DropdownSubContent className="min-w-40">
            <DropdownItem onClick={() => onExport('yaml')}>YAML</DropdownItem>
            <DropdownItem onClick={() => onExport('json')}>JSON</DropdownItem>
          </DropdownSubContent>
        </DropdownSub>
        <DropdownSeparator />
        <DropdownItem leftIcon="database" onClick={onReinit}>
          <HStack className="gap-2 items-center">
            Re-initialize…
            <span className="text-content-layout-3">(discards)</span>
          </HStack>
        </DropdownItem>
        <DropdownItem
          leftIcon="trash"
          onClick={onDelete}
          className="text-content-negative-soft hover:bg-surface-negative-soft hover:text-content-negative-soft focus:bg-surface-negative-soft focus:text-content-negative-soft"
        >
          Delete semantic layer…
        </DropdownItem>
      </DropdownContent>
    </Dropdown>
  )
}
