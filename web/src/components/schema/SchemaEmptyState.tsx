import { Card } from '@rs/ui-new/card'
import { EmptyState } from '@rs/ui-new/empty-state'

interface SchemaEmptyStateProps {
  target: string
  onInit: () => void
  isLoading?: boolean
}

/**
 * First use of the semantic layer for a target: one sentence of value and one
 * action, in the shared empty-state anatomy. [C-66, F-18]
 */
export function SchemaEmptyState({
  target,
  onInit,
  isLoading,
}: SchemaEmptyStateProps) {
  return (
    <Card className="w-full">
      <Card.Content className="p-0">
        <EmptyState
          icon="sparkles"
          title="No semantic layer yet"
          body={`Describe what ${target}'s tables and columns mean, so Ask and Analyze write better SQL against it.`}
          action={{
            label: 'Initialize schema',
            icon: 'sparkles',
            onClick: onInit,
            loading: isLoading,
          }}
        />
      </Card.Content>
    </Card>
  )
}
