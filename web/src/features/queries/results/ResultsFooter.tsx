import { Button } from '@rs/ui-new/button'
import { m } from '@rs/ui-new/motion'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { useId } from 'react'
import type { CompleteEvent } from '../../../lib/api'

export function ResultsFooter({
  results,
  target: targetProp,
  onAskFollowUp,
  hasExistingChat,
}: {
  results: CompleteEvent
  target?: string
  onAskFollowUp?: () => void
  hasExistingChat?: boolean
}) {
  const [planOpen, setPlanOpen] = useDisclosure({})
  const planId = useId()

  const metadata = results.formatted?.metadata
  const tokenUsage = results.llm_analysis?.token_usage
  const llmInfo = metadata?.llm_info
  const target = metadata?.target || targetProp
  const databaseEngine =
    metadata?.database_engine || results.explain_results?.database_engine
  const model = llmInfo?.model || 'claude'
  const tokens = llmInfo?.tokens || tokenUsage?.total || 0
  const cost = llmInfo?.cost || tokenUsage?.estimated_cost_usd || 0
  const hasModelMetadata = Boolean(llmInfo || tokenUsage)

  const metaSegments = [
    target ? `Target ${target}` : null,
    databaseEngine?.toUpperCase(),
    hasModelMetadata ? model : null,
    hasModelMetadata ? `${tokens.toLocaleString()} tokens` : null,
    hasModelMetadata ? `$${cost.toFixed(3)}` : null,
  ].filter(Boolean) as string[]

  const explainPlan = results.explain_results?.explain_plan
  const hasPlan = explainPlan != null && Object.keys(explainPlan).length > 0
  const planText = hasPlan ? JSON.stringify(explainPlan, null, 2) : ''
  const canAsk = Boolean(results.query_hash && onAskFollowUp)

  if (metaSegments.length === 0 && !hasPlan && !canAsk) return null

  return (
    <m.div
      className="border-t border-border-layout-1 pt-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
    >
      <HStack className="items-center justify-between gap-3 flex-wrap">
        {metaSegments.length > 0 ? (
          <Text level="caption" className="text-content-layout-3">
            {metaSegments.join(' · ')}
          </Text>
        ) : (
          <span />
        )}

        <HStack className="items-center gap-2 flex-wrap">
          {hasPlan ? (
            <Button
              variant="primary"
              modifier="link"
              size="small"
              label={planOpen ? 'Hide EXPLAIN plan' : 'View EXPLAIN plan'}
              icon="querypilot"
              iconPosition="left"
              aria-expanded={planOpen}
              aria-controls={planId}
              onClick={() => setPlanOpen(!planOpen)}
              className="no-underline"
            />
          ) : null}
          {canAsk ? (
            <Button
              variant="primary"
              modifier="link"
              size="small"
              label={
                hasExistingChat ? 'Continue conversation' : 'Ask a follow-up'
              }
              icon={hasExistingChat ? 'message-multiple' : 'sparkles'}
              iconPosition="left"
              onClick={onAskFollowUp}
              className="no-underline"
            />
          ) : null}
        </HStack>
      </HStack>

      {hasPlan && planOpen ? (
        <pre
          id={planId}
          className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border-layout-1 bg-surface-layout-2/60 p-4 text-mono-small text-content-layout-3"
        >
          {planText}
        </pre>
      ) : null}
    </m.div>
  )
}
