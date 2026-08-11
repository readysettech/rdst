'use client'

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { type ReactNode, useRef } from 'react'
import { Button } from '../element/button'
import { IconTile, type IconTileProps } from '../element/icon-tile'
import { HStack, VStack } from '../element/stack'
import { InlineNotice } from '../feedback/error-state'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from './modal'

export interface ConfirmDialogNotice {
  /** Accent of the tinted notice; defaults to `warning`. */
  accent?: 'negative' | 'warning' | 'info'
  icon?: IconStrokeName
  title?: string
  message: string
}

export interface ConfirmDialogProps {
  isOpen: boolean
  onClose: () => void
  onConfirm: () => void
  /** Visible + accessible dialog title (the consequence, phrased as a question). */
  title: string
  /** Optional node rendered next to the title (e.g. a `remote-target` tag). */
  titleAccessory?: ReactNode
  /** Sub-line under the title — the target and/or planned cost/load. */
  subtitle?: ReactNode
  /** Tinted notice naming the consequence/cost next to the action (§6.6). */
  notice?: ConfirmDialogNotice
  /** Extra body content (typed-confirm input, alternative-path hint). */
  children?: ReactNode
  confirmLabel: string
  /** Confirm colour: `negative` (destructive, default) or `primary`. */
  confirmVariant?: 'primary' | 'negative'
  confirmIcon?: IconStrokeName
  confirmDisabled?: boolean
  cancelLabel?: string
  loading?: boolean
  /** When loading, block dismissal so a running action isn't interrupted. */
  blockCloseWhileLoading?: boolean
  /** aria-describedby text; defaults to the notice message. */
  description?: string
  size?: 'base' | 'large'
}

function resolveIconAccent(
  notice: ConfirmDialogNotice | undefined,
  confirmVariant: ConfirmDialogProps['confirmVariant']
): IconTileProps['accent'] {
  if (notice?.accent === 'negative' || confirmVariant === 'negative') {
    return 'negative'
  }
  if (notice?.accent === 'warning') return 'warning'
  if (notice?.accent === 'info') return 'info'
  return 'primary'
}

/**
 * One shared destructive-confirmation dialog (design-system §6.6, launch-polish
 * Carry-over). Names the target + the consequence next to the action, offers a
 * red confirm and a safe Cancel, and focuses Cancel on open so the safe choice
 * is the default. Built on the shared `Modal`, so it inherits Radix's focus
 * trap + Escape-to-close and a real, required `Dialog.Title`/`aria-describedby`.
 * Elevation-3 (surface-overlay + the tallest shadow) marks it as the frontmost
 * surface.
 *
 * Folds the three bespoke confirms (SchemaReinitDialog, BenchmarkConfirmDialog,
 * demo teardown): the shared shell here, each site's specifics via `notice`,
 * `subtitle`, `titleAccessory` and `children`.
 */
export function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  titleAccessory,
  subtitle,
  notice,
  children,
  confirmLabel,
  confirmVariant = 'negative',
  confirmIcon,
  confirmDisabled,
  cancelLabel = 'Cancel',
  loading,
  blockCloseWhileLoading,
  description,
  size = 'base',
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  // When there's neither a visible subtitle nor an explicit sr-only description,
  // opt out of Radix's aria-describedby requirement (the visible notice carries
  // the context) rather than leave a dev "Missing Description" warning.
  const hasDescription = subtitle != null || description != null
  const identityIcon = notice?.icon ?? confirmIcon

  const handleOpenChange = (open: boolean) => {
    if (open) return
    // A running destructive action must complete; don't let Escape/overlay
    // clicks tear it down mid-flight.
    if (blockCloseWhileLoading && loading) return
    onClose()
  }

  return (
    <Modal open={isOpen} onOpenChange={handleOpenChange}>
      <ModalContentContainer open={isOpen}>
        <ModalContent
          size={size}
          className="gap-0 overflow-hidden bg-surface-layout-1 p-0 shadow-elevation-3"
          // A confirm is a two-button decision: no third X affordance (which
          // would also silently no-op while blockCloseWhileLoading holds).
          // Escape/overlay dismissal still routes through handleOpenChange.
          hideClose
          // A visible `subtitle` becomes the accessible description (the
          // ModalDescription below). Otherwise use only an explicit sr-only
          // `description` — never echo the visible notice message, which would
          // duplicate its text in the DOM. Radix allows a single Description
          // and doesn't require one.
          description={subtitle != null ? undefined : description}
          // Opt out of aria-describedby only when we have no description at all
          // (otherwise the ModalDescription/sr-only description wires it).
          {...(hasDescription ? {} : { 'aria-describedby': undefined })}
          // Safe default: focus Cancel, not the destructive confirm (§6.6).
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            cancelRef.current?.focus()
          }}
        >
          <header className="border-b-(length:--border-base) border-border-layout-1 px-6 py-5">
            <HStack className="items-start gap-3">
              {identityIcon ? (
                <IconTile
                  icon={identityIcon}
                  size="base"
                  accent={resolveIconAccent(notice, confirmVariant)}
                />
              ) : null}
              <VStack className="min-w-0 items-start gap-1">
                <HStack className="flex-wrap items-center gap-2">
                  <ModalTitle className="h-auto text-headline-4 text-content-layout-1">
                    {title}
                  </ModalTitle>
                  {titleAccessory}
                </HStack>
                {subtitle != null ? (
                  <ModalDescription className="text-body-small text-content-layout-3">
                    {subtitle}
                  </ModalDescription>
                ) : null}
              </VStack>
            </HStack>
          </header>

          {notice || children ? (
            <VStack className="items-stretch gap-4 p-6">
              {notice ? (
                <InlineNotice
                  accent={notice.accent ?? 'warning'}
                  icon={notice.icon}
                  // Empty string = no heading; InlineNotice renders the title
                  // conditionally and labels its glyph "Notice" instead.
                  title={notice.title ?? ''}
                  message={notice.message}
                />
              ) : null}
              {children}
            </VStack>
          ) : null}

          <footer className="border-t-(length:--border-base) border-border-layout-1 bg-surface-layout-1 px-6 py-4">
            <HStack className="justify-end gap-2">
              <Button
                ref={cancelRef}
                variant="primary"
                modifier="ghost"
                label={cancelLabel}
                onClick={onClose}
                disabled={loading}
              />
              <Button
                variant={confirmVariant}
                modifier="solid"
                icon={confirmIcon}
                iconPosition={confirmIcon ? 'left' : 'none'}
                label={confirmLabel}
                onClick={onConfirm}
                disabled={confirmDisabled}
                loading={loading}
              />
            </HStack>
          </footer>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
