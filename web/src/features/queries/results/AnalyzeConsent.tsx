import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { IconTile } from '@rs/ui-new/icon-tile'
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
 * database. Executing it is the consequence, so both presentations carry the
 * warning accent, the alert glyph and the same sentence: the drawer is the
 * path most people take, and it used to ask in a neutral card (Mike #3).
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
    <HStack className="items-center gap-3">
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
        isOpen={isOpen}
        onClose={onCancel}
        onConfirm={onConfirm}
        title={ANALYZE_CONSENT_TITLE}
        notice={{
          accent: 'warning',
          icon: 'alert',
          message: ANALYZE_CONSENT_MESSAGE,
        }}
        confirmLabel="Run analyze"
        confirmVariant="primary"
        confirmIcon="play"
        cancelLabel="Cancel"
      >
        {skipToggle}
      </ConfirmDialog>
    )
  }

  if (!isOpen) return null

  return (
    <div
      data-testid="analyze-consent-inline"
      // The same announcement the dialog's notice makes, since this is the
      // same prompt in a surface that cannot open a dialog.
      role="alert"
      className="rounded-xl border bg-surface-warning-soft/50 border-border-warning-soft shadow-glow-warning p-4"
    >
      <HStack className="items-start gap-3">
        <IconTile icon="alert" size="base" accent="warning" />
        <VStack className="min-w-0 flex-1 items-stretch gap-3">
          <VStack className="items-start gap-1">
            <Text level="label-medium" className="text-content-warning-soft">
              {ANALYZE_CONSENT_TITLE}
            </Text>
            <Text
              level="body-small"
              className="max-w-prose text-content-layout-2 leading-relaxed"
            >
              {ANALYZE_CONSENT_MESSAGE}
            </Text>
          </VStack>
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
                label="Run analyze"
                icon="play"
                iconPosition="left"
                onClick={onConfirm}
              />
            </HStack>
          </HStack>
        </VStack>
      </HStack>
    </div>
  )
}
