import { Button } from '@rs/ui-new/button'

interface SchemaInitButtonProps {
  onInit: () => void
  isLoading?: boolean
  hasExistingSchema?: boolean
  disabled?: boolean
}

export function SchemaInitButton({
  onInit,
  isLoading,
  hasExistingSchema,
  disabled,
}: SchemaInitButtonProps) {
  return (
    <Button
      modifier="ghost"
      label={
        isLoading
          ? 'Initializing...'
          : hasExistingSchema
            ? 'Re-init'
            : 'Initialize'
      }
      onClick={onInit}
      loading={isLoading}
      disabled={isLoading || disabled}
    />
  )
}
