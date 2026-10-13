import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Text } from '@rs/ui-new/text'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { clearKeyring } from '../../lib/api'
import { SettingsRow } from './SettingsGroup'

/** The development-build tool: wipe the OS keychain entries and start over. */
export function ClearKeyringRow() {
  const [confirming, setConfirming] = useState(false)
  const clear = useMutation({
    mutationFn: clearKeyring,
    onSuccess: () => window.location.reload(),
  })
  return (
    <>
      <SettingsRow
        title="Clear keyring"
        line="Removes stored secrets from the OS keychain, clears local AI access state, and reloads the page."
      >
        <Button
          variant="negative"
          modifier="ghost"
          size="small"
          label="Clear keyring"
          loading={clear.isPending}
          onClick={() => setConfirming(true)}
        />
      </SettingsRow>
      <ConfirmDialog
        isOpen={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => clear.mutate()}
        title="Clear the RDST keyring?"
        subtitle="Development environment"
        confirmLabel="Clear keyring"
        confirmVariant="negative"
        loading={clear.isPending}
        blockCloseWhileLoading
        notice={{
          accent: 'negative',
          icon: 'trash',
          title: 'Stored secrets will be removed',
          message:
            'This removes RDST secrets from the OS keychain, clears local AI access state, and reloads the app.',
        }}
      >
        {clear.error instanceof Error ? (
          <Text level="body-small" className="text-content-negative-soft">
            {clear.error.message}
          </Text>
        ) : null}
      </ConfirmDialog>
    </>
  )
}
