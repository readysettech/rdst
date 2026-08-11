import { Button } from '@rs/ui-new/button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import {
  QueryListEmptyState,
  QueryListErrorState,
} from '../../../components/query-list-state'
import { ParameterDialog } from '../../../components/top'
import { WorkspaceLayout } from '../../../components/workspace/WorkspaceLayout'
import { QueriesPaneSkeleton } from '../workspace/QueriesPaneSkeleton'
import { QUERY_LAB_VARIANT_COMPONENTS } from './QueryLabVariants'
import {
  QUERY_LAB_VARIANTS,
  type QueryLabVariant,
  useQueriesLabController,
} from './queryLabModel'

export function QueriesDesignLabPage({
  variant,
}: {
  variant: QueryLabVariant
}) {
  const navigate = useNavigate()
  const controller = useQueriesLabController()
  const concept = QUERY_LAB_VARIANTS[variant]
  const Variant = QUERY_LAB_VARIANT_COMPONENTS[variant]
  const variantCount = Object.keys(QUERY_LAB_VARIANTS).length

  return (
    <WorkspaceLayout
      title={concept.name}
      description={concept.hypothesis}
      icon="test-tube"
      panelId={`queries-lab-${variant}`}
      headerActions={
        <HStack className="items-center gap-2 ml-auto">
          <Tag
            size="base"
            variant="neutral"
            modifier="ghost"
            label={`Card ${variant} of ${variantCount}`}
          />
          <Button
            variant="primary"
            modifier="ghost"
            label="Back to Queries"
            icon="arrow-left"
            iconPosition="left"
            onClick={() => void navigate({ to: '/queries' })}
          />
        </HStack>
      }
    >
      {controller.registry.isLoading ? <QueriesPaneSkeleton /> : null}

      {!controller.registry.isLoading && controller.registry.listError ? (
        <QueryListErrorState
          title="Queries couldn't be loaded"
          message="Readyset couldn't load the query library for this design lab."
          trustworthy="No query was changed."
          error={controller.registry.listError}
          onRetry={() => void controller.registry.refetch()}
        />
      ) : null}

      {!controller.registry.isLoading &&
      !controller.registry.listError &&
      controller.lab.queries.length === 0 ? (
        <QueryListEmptyState
          icon="folder-file"
          title="No query available"
          body="Add or discover a query, then return to test this card with real workload evidence."
        />
      ) : null}

      {!controller.registry.isLoading &&
      !controller.registry.listError &&
      controller.lab.queries.length > 0 ? (
        <VStack className="items-stretch gap-4">
          <HStack className="items-center gap-2 flex-wrap">
            <Text level="caption" className="text-content-layout-3">
              Required contexts
            </Text>
            {[
              'Library',
              'New',
              'Cached',
              'Editable',
              'Selectable',
              'Expanded',
            ].map((label) => (
              <Tag
                key={label}
                size="small"
                variant="neutral"
                modifier="ghost"
                label={label}
              />
            ))}
          </HStack>
          <Variant controller={controller} />
        </VStack>
      ) : null}

      {controller.paramDialog ? (
        <ParameterDialog
          isOpen
          query={controller.paramDialog.sql}
          initialValues={
            controller.registry.queries.find(
              (query) => query.hash === controller.paramDialog?.hash
            )?.most_recent_params
          }
          submitLabel="Run test"
          submitIcon="speedometer"
          onClose={controller.closeParamDialog}
          onSubmit={controller.submitParamDialog}
        />
      ) : null}
    </WorkspaceLayout>
  )
}
