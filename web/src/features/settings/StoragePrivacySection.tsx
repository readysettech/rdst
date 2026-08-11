import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { CopyButton } from '@rs/ui-new/copy-button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import { SettingsSection } from '../../components/configure'

type StoragePrivacySectionProps = {
  dataDirectory: string
  resetPending: boolean
  resetError: string | null
  onReset: () => void
}

export function StoragePrivacySection({
  dataDirectory,
  resetPending,
  resetError,
  onReset,
}: StoragePrivacySectionProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)

  return (
    <SettingsSection
      title="Storage & privacy"
      description="Where your data lives, and what stays local."
    >
      <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4">
        <VStack className="gap-2 items-start">
          <Text level="body-small" className="text-content-layout-3">
            RDST stores its configuration, query library, semantic layer, and
            analysis history in this local directory.
          </Text>
          <HStack className="gap-2 items-center">
            <code className="text-xs bg-surface-raised px-2 py-1 rounded font-mono text-content-layout-2">
              {dataDirectory}
            </code>
            <CopyButton text={dataDirectory} />
          </HStack>
          <Text level="caption" className="text-content-layout-3">
            Contains connection configs, the query library, semantic layer, and
            analysis history. Passwords are stored in your system keyring, never
            in plain text.
          </Text>
          <HStack className="gap-3 items-center pt-2">
            <Button
              variant="negative"
              modifier="outline"
              label="Remove all local data"
              disabled={resetPending}
              onClick={() => setConfirmOpen(true)}
            />
          </HStack>
        </VStack>
      </div>
      <ConfirmDialog
        isOpen={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={onReset}
        title="Remove all local RDST data?"
        subtitle={dataDirectory}
        confirmLabel="Remove local data"
        confirmIcon="trash"
        confirmVariant="negative"
        loading={resetPending}
        blockCloseWhileLoading
        notice={{
          accent: 'negative',
          icon: 'trash',
          title: 'This cannot be undone',
          message:
            'Deletes local configuration, history, semantic data, and stored RDST keys. Your ~/.ssh directory is unchanged.',
        }}
      >
        {resetError ? (
          <Text level="body-small" className="text-content-negative-soft">
            {resetError}
          </Text>
        ) : null}
      </ConfirmDialog>
    </SettingsSection>
  )
}
