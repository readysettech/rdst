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
 *
 * Rows served while `isPlaceholder` is set belong to the previous request
 * (kept on screen for continuity), so a signature change adopts them only as
 * an uninitialized baseline: the settled rows for the new signature replace
 * them wholesale instead of counting as newly discovered.
 */
export function useStableQueryList({
  queries,
  signature,
  isLoading,
  isPlaceholder = false,
  hashAliases = {},
  revealHash,
}: {
  queries: QueryRegistryEntry[]
  signature: string
  isLoading: boolean
  isPlaceholder?: boolean
  hashAliases?: Record<string, string>
  revealHash?: string
}) {
  const currentHashes = useMemo(
    () => queries.map((query) => query.hash),
    [queries]
  )
  const settled = !isLoading && !isPlaceholder
  const [order, setOrder] = useState<StableOrder>(() => ({
    signature,
    hashes: currentHashes,
    initialized: settled,
  }))

  const signatureChanged = order.signature !== signature
  const effectiveHashes =
    signatureChanged || revealHash || !order.initialized
      ? currentHashes
      : order.hashes
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
  const pendingQueries =
    signatureChanged || !order.initialized
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
      (!order.initialized && settled)
    ) {
      setOrder({ signature, hashes: currentHashes, initialized: settled })
    }
  }, [
    currentHashes,
    settled,
    order.hashes,
    order.initialized,
    revealHash,
    signature,
    signatureChanged,
  ])

  const pendingNewCount = pendingQueries.filter((query) => query.is_new).length

  return {
    visibleQueries,
    keyForHash: (hash: string) => stableKeyByHash.get(hash) ?? hash,
    pendingCount: pendingQueries.length,
    pendingNewCount,
    pendingUpdatedCount: pendingQueries.length - pendingNewCount,
    revealPending: () =>
      setOrder({ signature, hashes: currentHashes, initialized: true }),
  }
}
