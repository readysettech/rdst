import { useEffect, useMemo, useRef } from 'react'
import { updateQueryParameters } from './api'
import { parameterValueKey } from './parameterSuggestions'
import type { Parameter } from './sqlParameters'
import { toBackendParamKey } from './sqlParameters'
import { useAutoParameterSuggestions } from './useAutoParameterSuggestions'

export interface AutoSuggestItem {
  /** Local key namespace for `values` (a registry hash, or a load-test identifier). */
  id: string
  /** Query registry hash; used for the suggestion fetch and the PATCH. */
  hash: string
  sql: string
  parameters: Parameter[]
}

export interface AutoSuggestFill {
  key: string
  value: string
  provenance: string
}

/**
 * Background parameter-suggestion pipeline for multi-query setup screens
 * (Compare, Load test): for every selected query that still has an unfilled
 * placeholder, fetches backend suggestions, fills any still-empty value with
 * the top suggestion, and persists the fill as source "suggested" so it
 * survives a later dialog open. A query the user already filled by hand is
 * left untouched; each (target, hash) pair is fetched and persisted at most
 * once thanks to TanStack Query's cache and a local dedupe set.
 */
export function useAutoSuggestParameters({
  target,
  items,
  values,
  keyFor,
  onApply,
}: {
  target: string | null | undefined
  items: AutoSuggestItem[]
  values: Record<string, string>
  keyFor: (id: string, parameter: Parameter) => string
  onApply: (fills: AutoSuggestFill[]) => void
}) {
  const pending = useMemo(
    () =>
      target
        ? items.filter((item) =>
            item.parameters.some(
              (parameter) => !values[keyFor(item.id, parameter)]?.trim()
            )
          )
        : [],
    [items, target, values, keyFor]
  )
  const suggestionQueries = useMemo(
    () =>
      pending.map((item) => ({
        hash: item.hash,
        sql: item.sql,
        target: target ?? '',
      })),
    [pending, target]
  )
  const suggestions = useAutoParameterSuggestions(suggestionQueries)
  const persisted = useRef(new Set<string>())

  useEffect(() => {
    persisted.current = new Set()
  }, [target])

  useEffect(() => {
    if (!target) return
    for (const item of pending) {
      const response = suggestions.get(item.hash)
      if (!response) continue
      const dedupeKey = `${target}:${item.hash}`
      if (persisted.current.has(dedupeKey)) continue

      const sampledByKey = new Map(
        response.placeholders.map((placeholder) => [
          placeholder.placeholder === '?'
            ? `?${placeholder.index}`
            : placeholder.placeholder,
          placeholder,
        ])
      )
      const fills: AutoSuggestFill[] = []
      const backendValues: Record<string, string> = {}
      for (const parameter of item.parameters) {
        const key = keyFor(item.id, parameter)
        if (values[key]?.trim()) continue
        const sampled = sampledByKey.get(parameterValueKey(parameter))
        const best = sampled?.suggestions[0]
        if (!best) continue
        fills.push({ key, value: best.value, provenance: best.provenance })
        backendValues[toBackendParamKey(parameter)] = best.value
      }
      if (fills.length === 0) continue

      persisted.current.add(dedupeKey)
      onApply(fills)
      if (Object.keys(backendValues).length > 0) {
        void updateQueryParameters(item.hash, backendValues, 'suggested').catch(
          () => {
            // Best-effort persistence; the fill already applied locally and
            // a later manual confirm will retry the save.
          }
        )
      }
    }
  }, [pending, suggestions, target, values, keyFor, onApply])
}
