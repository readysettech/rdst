import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { LoadTestController } from '@/features/caching/load-test/useLoadTestController'
import {
  PerformanceQueryCard,
  PerformanceQueryParameters,
  performanceParameterKey,
} from '@/features/caching/shared/PerformanceQueryList'
import { filterQueriesBySearch, queryDisplayName } from '@/lib/queryIdentity'
import { detectParameters } from '@/lib/sqlParameters'
import { sqlBlockReason } from './eligibility'

export function BenchmarkQueryPicker({
  controller,
}: {
  controller: LoadTestController
}) {
  const [search, setSearch] = useState('')
  const visible = filterQueriesBySearch(controller.queries, search)
  return (
    <section aria-label="Queries for this benchmark" className="space-y-3">
      <Text as="p" level="label-medium">
        Pick queries ({controller.selectedCount} selected)
      </Text>
      <BaseInputText
        aria-label="Search benchmark queries"
        placeholder="Search monitored or saved queries"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {controller.listError ? (
        <p role="alert">
          Queries didn't load. Try opening the benchmark again.
        </p>
      ) : null}
      <div className="max-h-72 space-y-2 overflow-y-auto">
        {visible.map((query) => {
          const identifier = query.tag || query.hash
          const reason = !query.sql.trim()
            ? "This query's text isn't available."
            : sqlBlockReason(query.sql)
          return (
            <PerformanceQueryCard
              key={query.hash}
              queryHash={query.hash}
              title={
                query.source === 'monitor-benchmark'
                  ? query.question || queryDisplayName(query)
                  : queryDisplayName(query)
              }
              sql={query.sql}
              selected={controller.selectedQueries.includes(identifier)}
              disabled={Boolean(reason)}
              onSelect={() => controller.toggleQuery(identifier)}
              parameterCount={detectParameters(query.sql).length}
              meta={
                reason ??
                (query.source === 'monitor-benchmark'
                  ? 'Monitored query'
                  : 'Saved query')
              }
            />
          )
        })}
      </div>
      {!visible.length ? (
        <p className="text-body-small text-content-layout-3">
          {controller.registryLoading
            ? 'Reading queries'
            : 'No matching queries yet.'}
        </p>
      ) : null}
      {controller.queriesWithParameters.map((query) => (
        <div
          key={query.identifier}
          className="rounded-panel border border-border-layout-1"
        >
          <Text as="p" level="label-small" className="px-4 pt-3">
            Values for {query.name}
          </Text>
          <PerformanceQueryParameters
            ownerId={query.identifier}
            inputPrefix="benchmark"
            parameters={query.parameters}
            values={controller.paramValues}
            sources={controller.parameterSources}
            onValueChange={(parameter, value) =>
              controller.updateParameter(
                performanceParameterKey(query.identifier, parameter),
                value
              )
            }
          />
        </div>
      ))}
    </section>
  )
}
