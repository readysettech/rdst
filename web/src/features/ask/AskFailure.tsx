// The query failed: one warning line naming the database and its error, a ghost "Edit
// question" and the solid "Try again", with the refused SQL in view.
import { Button, buttonStyles } from '@rs/ui-new/button'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { AskErrorEvent } from '@/lib/ask'
import {
  classifyError,
  isModelContextLimitError,
  isTrialExhaustedError,
  TRIAL_EXHAUSTED_MESSAGE,
} from '@/lib/errorContract'
import { SqlDisclosure } from './AskAnswer'

const ghostLink = buttonStyles({
  variant: 'primary',
  modifier: 'ghost',
  size: 'base',
}).root()

export function AskFailure({
  error,
  target,
  sql,
  limitAdded,
  onEdit,
  onRetry,
}: {
  error: AskErrorEvent
  target: string | null
  sql: string
  limitAdded: boolean
  onEdit: () => void
  onRetry: () => void
}) {
  const [sqlOpen, setSqlOpen] = useState(true)
  const envelope = {
    code: error.code || error.category || '',
    category: error.category,
    target: error.target || target || undefined,
    message: error.message,
  }
  // A schema too large for the model: neither a credential problem nor one a retry fixes.
  const tooLarge = isModelContextLimitError(envelope)
  const trialExhausted = !tooLarge && isTrialExhaustedError(envelope)
  const needsKey =
    !tooLarge &&
    !trialExhausted &&
    ['provider', 'rdst-service'].includes(classifyError(envelope))
  const refused = error.phase === 'execute' && (error.target || target)
  const label = trialExhausted
    ? TRIAL_EXHAUSTED_MESSAGE
    : refused
      ? `${error.target || target} refused the query: ${error.message.replace(/\.$/, '')}.`
      : error.message
  const hint =
    refused && /permission denied for table/i.test(error.message)
      ? 'Ask RDST to try without that table, or grant SELECT.'
      : null
  return (
    <div data-testid="ask-failure" className="mt-9">
      <InlineNotice
        tone="quiet"
        accent="warning"
        title={hint ? label : ''}
        message={hint ?? label}
      />
      <div className="mt-5 flex gap-1.5">
        <Button
          variant="primary"
          modifier="ghost"
          label="Edit question"
          onClick={onEdit}
        />
        {tooLarge ? null : trialExhausted || needsKey ? (
          <Link to="/account-login" className={ghostLink}>
            Sign in
          </Link>
        ) : (
          <Button
            variant="primary"
            modifier="solid"
            label="Try again"
            onClick={onRetry}
          />
        )}
      </div>
      {sql && (
        <SqlDisclosure
          title="SQL that failed"
          sql={sql}
          limitAdded={limitAdded}
          open={sqlOpen}
          onToggle={() => setSqlOpen((value) => !value)}
        />
      )}
    </div>
  )
}
