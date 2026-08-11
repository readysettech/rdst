import { useQuery } from '@tanstack/react-query'
import { fetchSchema } from '../../lib/api'
import type { SchemaInfo } from '../SQLEditor'

export interface SQLSchemaState {
  schema?: SchemaInfo
  isLoading: boolean
}

export function useSQLSchema(
  target?: string | null,
  enabled = true
): SQLSchemaState {
  const {
    data: schemaData,
    isLoading,
    isFetching,
  } = useQuery({
    queryKey: ['schema', target],
    queryFn: () => fetchSchema(target || undefined),
    staleTime: 5 * 60 * 1000,
    enabled: enabled && !!target,
  })

  const schema: SchemaInfo | undefined =
    schemaData?.tables && Object.keys(schemaData.tables).length > 0
      ? { tables: schemaData.tables, dialect: schemaData.dialect }
      : undefined

  return {
    schema,
    isLoading: !!target && enabled && (isLoading || isFetching),
  }
}
