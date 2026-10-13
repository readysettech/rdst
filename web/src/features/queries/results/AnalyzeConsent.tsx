import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { InlineNotice } from '@rs/ui-new/error-state'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

/** Asked in the same words wherever it is asked. */
export const ANALYZE_CONSENT_TITLE = 'Run EXPLAIN ANALYZE?'
export const ANALYZE_CONSENT_MESSAGE =
  'Analyze runs EXPLAIN ANALYZE, which executes your query once against the database to measure it. Cancel if this query should not be executed.'

interface AnalyzeConsentProps {
  /**
   * `'modal'` on `/results`; `'inline'` in the analyze drawer, which is
   * already an overlay and cannot host a second dismissable layer.
   */
  presentation: 'modal' | 'inline'
  isOpen: boolean
  skipFuturePrompts: boolean
  onSkipFuturePrompts: (skip: boolean) => void
  onCancel: () => void
  onConfirm: () => void
}

/**
 * The consent for a measurement that executes the user's query against their
 * database. Both presentations ask in the same two sentences: a quiet dialog on
 * `/results`, a quiet inline-alert line in the drawer.
 */
export function AnalyzeConsent({
  presentation,
  isOpen,
  skipFuturePrompts,
  onSkipFuturePrompts,
  onCancel,
  onConfirm,
}: AnalyzeConsentProps) {
  const skipToggle = (
    <HStack className="items-center gap-2">
      <BaseInputCheckbox
        checked={skipFuturePrompts}
        onCheckedChange={(checked) => onSkipFuturePrompts(checked === true)}
        aria-label="Don't ask again"
      />
      <Text level="body-small" className="text-content-layout-2">
        Don't ask again
      </Text>
    </HStack>
  )

  if (presentation === 'modal') {
    return (
      <ConfirmDialog
        tone="quiet"
        isOpen={isOpen}
        onClose={onCancel}
        onConfirm={onConfirm}
        title={ANALYZE_CONSENT_TITLE}
        subtitle={ANALYZE_CONSENT_MESSAGE}
        confirmLabel="Analyze"
        confirmVariant="primary"
        cancelLabel="Cancel"
      >
        {skipToggle}
      </ConfirmDialog>
    )
  }

  if (!isOpen) return null

  return (
    <VStack
      data-testid="analyze-consent-inline"
      className="items-stretch gap-3"
    >
      <InlineNotice
        tone="quiet"
        accent="warning"
        title={ANALYZE_CONSENT_TITLE}
        message={ANALYZE_CONSENT_MESSAGE}
      />
      <HStack className="flex-wrap items-center justify-between gap-3">
        {skipToggle}
        <HStack className="items-center gap-2">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Cancel"
            onClick={onCancel}
          />
          <Button
            variant="primary"
            modifier="solid"
            size="small"
            label="Analyze"
            onClick={onConfirm}
          />
        </HStack>
      </HStack>
    </VStack>
  )
}
