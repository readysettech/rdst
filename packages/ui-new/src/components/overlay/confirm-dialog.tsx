'use client'

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Button } from '../element/button'
import { IconTile, type IconTileProps } from '../element/icon-tile'
import { HStack, VStack } from '../element/stack'
import { Text } from '../element/text'
import { InlineNotice } from '../feedback/error-state'
import { BaseInputText } from '../form/base/input-text'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from './modal'
import { Scrollable } from './scrollable'

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
  /**
   * Typed-confirm tier: the confirm stays disabled until the user types this
   * exact string (the resource's own name). Reserved for an action that
   * destroys something the UI cannot recreate, or that runs real load against
   * a database the user does not own.
   */
  requireTyped?: string
  /** Extra body content (an alternative-path hint, a form). */
  children?: ReactNode
  confirmLabel: string
  /** Confirm colour: `negative` (destructive, default) or `primary`. */
  confirmVariant?: 'primary' | 'negative'
  confirmIcon?: IconStrokeName
  confirmDisabled?: boolean
  /**
   * Why the confirm is unavailable, in the user's terms. Required alongside
   * `confirmDisabled`: a dialog that opens on a dead button has to say what
   * would make it live (§10).
   */
  confirmDisabledReason?: string
  cancelLabel?: string
  loading?: boolean
  /** When loading, block dismissal so a running action isn't interrupted. */
  blockCloseWhileLoading?: boolean
  /** aria-describedby text; defaults to the notice message. */
  description?: string
  size?: 'base' | 'large'
  /**
   * `quiet`: the 480px raised dialog. A 17px title with the close mark, the subtitle
   * and notice as plain paragraphs (no tinted box, no icon tile), and a footer with an
   * optional `note`, the ghost cancel and one solid confirm without an icon.
   */
  tone?: 'default' | 'quiet'
  /** Caption at the left of the footer (e.g. how long the action takes). */
  note?: ReactNode
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
 *
 * Two tiers live here (GUIDELINES §10). A plain confirm names the consequence
 * and takes one click. `requireTyped` adds the stronger tier: the confirm stays
 * disabled until the resource's own name is typed back.
 */
export function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  titleAccessory,
  subtitle,
  notice,
  requireTyped,
  children,
  confirmLabel,
  confirmVariant = 'negative',
  confirmIcon,
  confirmDisabled,
  confirmDisabledReason,
  cancelLabel = 'Cancel',
  loading,
  blockCloseWhileLoading,
  description,
  size = 'base',
  tone = 'default',
  note,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const typedRef = useRef<HTMLInputElement>(null)
  const [typed, setTyped] = useState('')

  // Clear the typed confirmation on close so the action can never arrive
  // pre-confirmed from a previous open.
  useEffect(() => {
    if (!isOpen) setTyped('')
  }, [isOpen])

  const typedMismatch =
    requireTyped != null && typed.trim() !== requireTyped.trim()

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

  const onOpenAutoFocus = (e: Event) => {
    e.preventDefault()
    if (requireTyped != null) {
      typedRef.current?.focus()
      return
    }
    cancelRef.current?.focus()
  }

  const typedField =
    requireTyped != null ? (
      <VStack className="gap-2 items-stretch">
        <Text level="label-small" className="text-content-layout-2">
          Type{' '}
          <span className="font-medium text-content-layout-1">
            {requireTyped}
          </span>{' '}
          to confirm
        </Text>
        <BaseInputText
          ref={typedRef}
          name="confirm-typed"
          placeholder={requireTyped}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          error={typed.length > 0 && typedMismatch}
          autoComplete="off"
        />
      </VStack>
    ) : null

  const confirmButton = (icon?: IconStrokeName) => (
    <Button
      variant={confirmVariant}
      modifier="solid"
      icon={icon}
      label={confirmLabel}
      onClick={() => {
        if (typedMismatch) return
        onConfirm()
      }}
      disabled={confirmDisabled || typedMismatch}
      loading={loading}
    />
  )

  const cancelButton = (
    <Button
      ref={cancelRef}
      variant="primary"
      modifier="ghost"
      label={cancelLabel}
      onClick={onClose}
      disabled={loading}
    />
  )

  if (tone === 'quiet') {
    const caption =
      confirmDisabled && confirmDisabledReason ? confirmDisabledReason : note
    return (
      <Modal open={isOpen} onOpenChange={handleOpenChange}>
        <ModalContentContainer open={isOpen}>
          <ModalContent
            tone="quiet"
            className="flex max-h-[calc(100dvh-2rem)] flex-col"
            hideClose={blockCloseWhileLoading && loading}
            description={subtitle != null ? undefined : description}
            {...(hasDescription ? {} : { 'aria-describedby': undefined })}
            onOpenAutoFocus={onOpenAutoFocus}
          >
            <HStack className="shrink-0 flex-wrap items-center gap-2 pt-4.5 pr-14 pl-6">
              <ModalTitle>{title}</ModalTitle>
              {titleAccessory}
            </HStack>
            <Scrollable
              rootClassName="min-h-0 flex-1"
              className="px-6 pt-2 pb-5"
            >
              <VStack className="items-stretch gap-3">
                {subtitle != null ? (
                  <ModalDescription>{subtitle}</ModalDescription>
                ) : null}
                {notice ? (
                  <Text level="body-small" className="text-content-layout-2">
                    {notice.title ? `${notice.title} ` : null}
                    {notice.message}
                  </Text>
                ) : null}
                {typedField}
                {children}
              </VStack>
            </Scrollable>
            <HStack className="shrink-0 items-center gap-3 border-t border-border-layout-2 px-6 py-3.5">
              <Text
                level="caption"
                className={
                  confirmDisabled && confirmDisabledReason
                    ? 'flex-1 text-content-warning-soft'
                    : 'flex-1 text-content-layout-3'
                }
              >
                {caption}
              </Text>
              {cancelButton}
              {confirmButton()}
            </HStack>
          </ModalContent>
        </ModalContentContainer>
      </Modal>
    )
  }

  return (
    <Modal open={isOpen} onOpenChange={handleOpenChange}>
      <ModalContentContainer open={isOpen}>
        <ModalContent
          size={size}
          // Never taller than the window: the body scrolls, and the actions stay in view.
          className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden bg-surface-layout-1 p-0 shadow-elevation-3"
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
          // The typed tier focuses its own input instead — equally safe, and
          // the one thing that turns the confirm from dead into live, so it
          // cannot read as a dialog that opened broken (§10).
          onOpenAutoFocus={onOpenAutoFocus}
        >
          <header className="shrink-0 border-b-(length:--border-base) border-border-layout-1 px-6 py-5">
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

          {notice || requireTyped != null || children ? (
            <VStack className="min-h-0 flex-1 items-stretch gap-4 overflow-y-auto p-6">
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
              {typedField}
              {children}
            </VStack>
          ) : null}

          <footer className="shrink-0 border-t-(length:--border-base) border-border-layout-1 bg-surface-layout-1 px-6 py-4">
            <HStack className="flex-wrap items-center justify-end gap-2">
              {confirmDisabled && confirmDisabledReason ? (
                <Text
                  level="caption"
                  className="mr-auto text-content-warning-soft"
                >
                  {confirmDisabledReason}
                </Text>
              ) : note ? (
                <Text level="caption" className="mr-auto text-content-layout-3">
                  {note}
                </Text>
              ) : null}
              {cancelButton}
              {confirmButton(confirmIcon)}
            </HStack>
          </footer>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
