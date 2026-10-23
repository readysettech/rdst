// Why RDST can't connect to a database it has, and every way to fix it in one place: the
// connection details, the provider letting RDST in, and the user's coding agent. It shows
// wherever the connection fails, the database's page included, whether or not monitoring
// was ever turned on.
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { ConnectionFailureActions } from '@/components/ConnectionFailureActions'
import type { PrerequisitesState } from '@/features/prerequisites/usePrerequisites'
import { AgentFix } from './AgentFix'

export function ConnectionProblem({
  target,
  state,
  onCheck,
}: {
  target: string
  state: Pick<PrerequisitesState, 'result' | 'error'>
  /** Check the connection again, after a fix. */
  onCheck: () => void
}) {
  const message = state.error?.message ?? state.result?.connection_error
  return (
    <>
      <section
        aria-label={`RDST can't connect to ${target}`}
        className="mb-4 space-y-2 rounded-panel border border-border-warning-soft bg-surface-warning-soft px-4 py-3"
        data-testid="enable-connection-actions"
      >
        <Text as="p" level="label-medium" className="text-content-layout-1">
          {`RDST can't connect to ${target}`}
        </Text>
        {message && (
          <Text
            as="p"
            level="caption"
            className="ph-mask text-content-layout-2"
          >
            {message}
          </Text>
        )}
        <ConnectionFailureActions
          failure={{
            target,
            message: state.result?.connection_error,
            category: state.result?.connection_category,
          }}
          featureRecovery
          onResolved={onCheck}
        />
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Check again"
          className="-ml-2"
          onClick={onCheck}
          data-testid="enable-check-again"
        />
      </section>
      <AgentFix target={target} onFinished={onCheck} problem={message} />
    </>
  )
}
