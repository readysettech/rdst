import type { QueryClient } from '@tanstack/react-query'

// Everything a saved target credential changes: the fleet list the rows are
// read from, the global no-targets lockout, and the env requirements that say
// whether the password reached the config.
const TARGET_QUERY_KEYS = [
  ['fleet-targets'],
  ['status'],
  ['init-status'],
  ['env-requirements'],
]

export async function invalidateTargetQueries(queryClient: QueryClient) {
  await Promise.all(
    TARGET_QUERY_KEYS.map((queryKey) =>
      queryClient.invalidateQueries({ queryKey })
    )
  )
}
