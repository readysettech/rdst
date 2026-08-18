import { Button } from '@rs/ui-new/button'
import { VStack } from '@rs/ui-new/stack'
import { ParameterSuggestionSummary } from '../../../components/ParameterSuggestionSummary'

/**
 * Suggest-values unit for a performance-test setup screen: the action button,
 * its busy state, the pass-outcome message, and the schema-initialization CTA.
 * Both Compare and Load test render this directly under the run-summary rows
 * in their side panel, so the two screens share one placement by construction.
 */
export function SuggestValuesPanel({
  hasParameters,
  suggesting,
  missingParameterCount,
  message,
  schemaUnavailable,
  onSuggest,
}: {
  /** Whether any selected query exposes parameters; renders nothing otherwise. */
  hasParameters: boolean
  suggesting: boolean
  missingParameterCount: number
  message: string | null
  schemaUnavailable: boolean
  onSuggest: () => void
}) {
  if (!hasParameters) return null
  return (
    <VStack className="items-stretch gap-1">
      <Button
        size="small"
        variant="primary"
        modifier="outline"
        icon="sparkles"
        iconPosition="left"
        label="Suggest values"
        loading={suggesting}
        disabled={missingParameterCount === 0}
        onClick={onSuggest}
      />
      <ParameterSuggestionSummary
        message={message}
        schemaUnavailable={schemaUnavailable}
      />
    </VStack>
  )
}
