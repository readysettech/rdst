import { HStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { fetchSchemaStatus } from '../lib/api'

/**
 * The Ask target's schema-readiness badge, lifted out of the old context bar to
 * sit inline beside the "Ask" page title [Ask 1; USE-002/003 fold the label
 * into the value]. Owns its own schema-status query so the header stays the
 * single canonical place the semantic layer is named [S5 target-redundancy].
 */
export function SemanticLayerBadge({ target }: { target?: string | null }) {
  const navigate = useNavigate()
  const { data: schemaStatus } = useQuery({
    queryKey: ['ask', 'schema-status', target],
    queryFn: ({ signal }) => fetchSchemaStatus(target!, signal),
    staleTime: 60_000,
    enabled: !!target,
  })

  if (!target) return null
  const hasSemanticLayer = schemaStatus?.exists === true

  return (
    <HStack className="gap-2 items-center">
      <Tag
        size="small"
        variant={hasSemanticLayer ? 'positive' : 'warning'}
        modifier="ghost"
        label={
          hasSemanticLayer
            ? `Semantic layer · ${schemaStatus?.tables ?? 0} tables`
            : 'Live introspection — no semantic layer'
        }
      />
      {!hasSemanticLayer && (
        <button
          type="button"
          onClick={() => navigate({ to: '/schema' })}
          className="text-content-primary-soft text-label-small hover:underline"
        >
          Discover schema →
        </button>
      )}
    </HStack>
  )
}
