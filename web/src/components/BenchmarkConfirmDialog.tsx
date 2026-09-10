import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Tag } from '@rs/ui-new/tag'

interface BenchmarkConfirmDialogProps {
  isOpen: boolean
  target: string
  /** True when the destination host is not loopback (a remote / possibly-prod DB). */
  isRemote: boolean
  queryCount: number
  /** Human load summary, e.g. "100ms interval · 30s" or "4 workers · 30s". */
  loadSummary: string
  /** True when this run also drives the Readyset lane beside the origin. */
  includesReadyset: boolean
  /** Theoretical request ceiling, or null for a tight loop (interval 0). */
  estimatedExecutions: number | null
  /** Server-side hard cap on total executions (shown for the tight-loop case). */
  executionCap: number
  onConfirm: () => void
  onClose: () => void
}

/**
 * Pre-flight confirmation for a Load test run.
 *
 * Names the destination and the planned load before any real DB work. Local
 * targets get a single explicit confirm; non-local (remote) targets get a
 * distinct, stronger gate — a red warning plus a typed-confirmation of the
 * target name — because one click can otherwise hammer a production database.
 * The server-side read-only + cap rails hold regardless of this dialog.
 *
 * Folded onto the shared `ConfirmDialog` primitive (T19/C-08): the shell,
 * focus-trap, Escape, titled dialog and the typed-confirm tier all come from
 * the primitive.
 */
export function BenchmarkConfirmDialog({
  isOpen,
  target,
  isRemote,
  queryCount,
  loadSummary,
  includesReadyset,
  estimatedExecutions,
  executionCap,
  onConfirm,
  onClose,
}: BenchmarkConfirmDialogProps) {
  // Named only when the run drives it: the second lane makes the run
  // heavier and leaves temporary caches on the sandbox until it ends.
  const readysetNote = includesReadyset
    ? ' This run also drives Readyset alongside it, creating a temporary cache per query that is dropped when the run ends.'
    : ''

  const execLabel =
    estimatedExecutions === null
      ? `hard cap ${executionCap.toLocaleString()} requests`
      : `up to ~${estimatedExecutions.toLocaleString()} requests before query latency`

  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onConfirm}
      title={`Run load test against ${target}?`}
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
              message: `${target} is not a local target. Load tests run real read-only traffic against a remote — possibly production — database.${readysetNote} Type the target name below to confirm you intend to run load against it.`,
            }
          : {
              accent: 'warning',
              icon: 'play',
              title: 'This runs real database load',
              message: `The selected queries will execute repeatedly against ${target} for the configured duration.${readysetNote} Only read-only SELECT queries are allowed — writes are rejected server-side.`,
            }
      }
      confirmLabel={isRemote ? 'Run against remote' : 'Run load test'}
      confirmVariant={isRemote ? 'negative' : 'primary'}
      confirmIcon="play"
      requireTyped={isRemote ? target : undefined}
    />
  )
}
