import { useEffect, useMemo, useState } from 'react'
import type { QueryRegistryEntry } from '../../../lib/useQueryRegistry'

type StableOrder = {
  signature: string
  hashes: string[]
  initialized: boolean
}

/**
 * Keep the rendered list still while discovery refreshes evidence underneath
 * it. New matching rows wait behind an explicit reveal action; changing a
 * filter or sort is itself an explicit reorder and adopts the new order.
 */
export function useStableQueryList({
  queries,
  signature,
  isLoading,
  hashAliases = {},
  revealHash,
}: {
  queries: QueryRegistryEntry[]
  signature: string
  isLoading: boolean
  hashAliases?: Record<string, string>
  revealHash?: string
}) {
  const currentHashes = useMemo(
    () => queries.map((query) => query.hash),
    [queries]
  )
  const [order, setOrder] = useState<StableOrder>(() => ({
    signature,
    hashes: currentHashes,
    initialized: !isLoading,
  }))

  const signatureChanged = order.signature !== signature
  const effectiveHashes =
    signatureChanged || revealHash ? currentHashes : order.hashes
  const queryByHash = useMemo(
    () => new Map(queries.map((query) => [query.hash, query])),
    [queries]
  )
  const resolveHash = (hash: string) => {
    const visited = new Set<string>()
    let resolved = hash
    while (hashAliases[resolved] && !visited.has(resolved)) {
      visited.add(resolved)
      resolved = hashAliases[resolved]
    }
    return resolved
  }
  const visibleQueries = effectiveHashes
    .map((hash) => queryByHash.get(resolveHash(hash)))
    .filter((query): query is QueryRegistryEntry => Boolean(query))
  const stableKeyByHash = new Map(
    effectiveHashes.map((hash) => [resolveHash(hash), hash])
  )
  const accepted = new Set(effectiveHashes.map(resolveHash))
  const pendingQueries = signatureChanged
    ? []
    : queries.filter((query) => !accepted.has(query.hash))

  useEffect(() => {
    const revealOrderChanged =
      Boolean(revealHash) &&
      (order.hashes.length !== currentHashes.length ||
        order.hashes.some((hash, index) => hash !== currentHashes[index]))
    if (
      signatureChanged ||
      revealOrderChanged ||
      (!order.initialized && !isLoading)
    ) {
      setOrder({ signature, hashes: currentHashes, initialized: true })
    }
  }, [
    currentHashes,
    isLoading,
    order.hashes,
    order.initialized,
    revealHash,
    signature,
    signatureChanged,
  ])

  return {
    visibleQueries,
    keyForHash: (hash: string) => stableKeyByHash.get(hash) ?? hash,
    pendingCount: pendingQueries.length,
    revealPending: () =>
      setOrder({ signature, hashes: currentHashes, initialized: true }),
  }
}
