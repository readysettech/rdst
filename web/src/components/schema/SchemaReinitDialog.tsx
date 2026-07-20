import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Text } from '@rs/ui-new/text'

interface SchemaReinitDialogProps {
  isOpen: boolean
  target: string
  isLoading?: boolean
  onConfirm: () => void
  onClose: () => void
}

/**
 * Destructive-confirmation guard for Schema "Re-init" (B4/T4).
 *
 * Re-init re-introspects the database and overwrites the semantic layer with a
 * fresh one — it does NOT merge annotations the way Refresh does, so every
 * human- and AI-authored annotation is permanently lost. This dialog names that
 * consequence explicitly before the destructive `initSchema(force: true)` call
 * runs. Cancelling (or dismissing) never calls the backend, so annotations are
 * preserved.
 *
 * Folded onto the shared `ConfirmDialog` primitive (T19/C-08) — one destructive
 * dialog vocabulary, with focus-trap + Escape + a titled dialog for free.
 */
export function SchemaReinitDialog({
  isOpen,
  target,
  isLoading,
  onConfirm,
  onClose,
}: SchemaReinitDialogProps) {
  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onConfirm}
      title="Re-initialize semantic layer?"
      subtitle={`Target: ${target}`}
      notice={{
        accent: 'warning',
        icon: 'alert',
        title: 'This discards every annotation',
        message:
          'Re-init rebuilds the layer from a fresh introspection and does not merge existing annotations. All table and column descriptions, enum meanings, business terms, metrics, and relationships — human and AI-authored — will be permanently deleted. This cannot be undone.',
      }}
      confirmLabel="Discard & Re-init"
      confirmVariant="negative"
      loading={isLoading}
      blockCloseWhileLoading
    >
      <Text level="body-small" className="text-content-layout-2">
        To pick up structural changes while keeping your annotations, cancel and
        use <span className="text-content-layout-1 font-medium">Refresh</span>{' '}
        instead.
      </Text>
    </ConfirmDialog>
  )
}
