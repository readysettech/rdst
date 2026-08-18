import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link } from '@tanstack/react-router'

/**
 * Caption line under a Suggest-values action. When the pass ran without
 * schema evidence, adds a pointer to the Schema page, where the semantic
 * layer for the target can be initialized.
 */
export function ParameterSuggestionSummary({
  message,
  schemaUnavailable = false,
}: {
  message: string | null
  schemaUnavailable?: boolean
}) {
  if (!message) return null
  return (
    <VStack className="items-start gap-0.5">
      <Text level="caption" className="text-content-layout-2">
        {message}
      </Text>
      {schemaUnavailable ? (
        <Text level="caption" className="text-content-layout-3">
          <Link
            to="/schema"
            className="text-content-primary-solid hover:underline"
          >
            Initialize the semantic layer
          </Link>{' '}
          to enable schema-based suggestions.
        </Text>
      ) : null}
    </VStack>
  )
}
