import { BaseInputText } from '@rs/ui-new/base-input-text'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'

interface BenchmarkConfirmDialogProps {
  isOpen: boolean
  target: string
  /** True when the destination host is not loopback (a remote / possibly-prod DB). */
  isRemote: boolean
  queryCount: number
  /** Human load summary, e.g. "100ms interval · 30s" or "4 workers · 30s". */
  loadSummary: string
  /** Estimated total executions, or null for an unbounded tight loop (interval 0). */
  estimatedExecutions: number | null
  /** Server-side hard cap on total executions (shown for the tight-loop case). */
  executionCap: number
  onConfirm: () => void
  onClose: () => void
}

/**
 * Pre-flight confirmation for a benchmark run (B5/T5).
 *
 * Names the destination and the planned load before any real DB work. Local
 * targets get a single explicit confirm; non-local (remote) targets get a
 * distinct, stronger gate — a red warning plus a typed-confirmation of the
 * target name — because one click can otherwise hammer a production database.
 * The server-side read-only + cap rails hold regardless of this dialog.
 *
 * Folded onto the shared `ConfirmDialog` primitive (T19/C-08): the shell,
 * focus-trap, Escape and titled dialog come from the primitive; the remote
 * typed-confirm input is this site's specific body.
 */
export function BenchmarkConfirmDialog({
  isOpen,
  target,
  isRemote,
  queryCount,
  loadSummary,
  estimatedExecutions,
  executionCap,
  onConfirm,
  onClose,
}: BenchmarkConfirmDialogProps) {
  const [typed, setTyped] = useState('')

  // Clear the typed confirmation each time the dialog closes so a remote run
  // can never be pre-confirmed from a previous open.
  useEffect(() => {
    if (!isOpen) setTyped('')
  }, [isOpen])

  const remoteConfirmed = !isRemote || typed.trim() === target

  const execLabel =
    estimatedExecutions === null
      ? `up to ${executionCap.toLocaleString()} executions`
      : `~${estimatedExecutions.toLocaleString()} executions`

  const handleConfirm = () => {
    if (!remoteConfirmed) return
    onConfirm()
  }

  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={handleConfirm}
      title={`Run benchmark against ${target}?`}
      titleAccessory={
        isRemote ? (
          <Tag
            size="small"
            variant="negative"
            modifier="solid"
            label="remote-target"
          />
        ) : undefined
      }
      subtitle={`${queryCount} ${queryCount === 1 ? 'query' : 'queries'} · ${loadSummary} · ${execLabel}`}
      notice={
        isRemote
          ? {
              accent: 'negative',
              icon: 'alert',
              title: 'This is a remote database',
              message: `${target} is not a local target. Benchmarking runs real read-only load against a remote — possibly production — database. Type the target name below to confirm you intend to run load against it.`,
            }
          : {
              accent: 'warning',
              icon: 'play',
              title: 'This runs real database load',
              message: `The selected queries will execute repeatedly against ${target} for the configured duration. Only read-only SELECT queries are allowed — writes are rejected server-side.`,
            }
      }
      confirmLabel={isRemote ? 'Run against remote' : 'Run benchmark'}
      confirmVariant={isRemote ? 'negative' : 'primary'}
      confirmIcon="play"
      confirmDisabled={!remoteConfirmed}
    >
      {isRemote && (
        <VStack className="gap-2 items-stretch">
          <Text level="label-small" className="text-content-layout-2">
            Type{' '}
            <span className="text-content-layout-1 font-medium">{target}</span>{' '}
            to confirm
          </Text>
          <BaseInputText
            name="benchmark-remote-confirm"
            placeholder={target}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            error={typed.length > 0 && !remoteConfirmed}
            autoComplete="off"
          />
        </VStack>
      )}
    </ConfirmDialog>
  )
}
