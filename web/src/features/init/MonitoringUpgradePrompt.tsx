import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRouterState } from '@tanstack/react-router'
import { useState } from 'react'
import {
  EnableMonitoringDialog,
  SIGNED_OUT_LINE,
} from '@/features/database/settings/EnableMonitoringDialog'
import type { components } from '@/lib/api.generated'
import { useMonitoringLimit } from '@/lib/monitor/hooks'

const upgradeKey = ['monitoring-upgrade'] as const

type UpgradeState = components['schemas']['MonitoringUpgradeResponse']

async function fetchUpgrade(): Promise<UpgradeState> {
  const response = await fetch('/api/init/monitoring-upgrade')
  if (!response.ok) throw new Error("Couldn't check the monitoring invitation")
  return response.json()
}

export function MonitoringUpgradePrompt() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const upgrade = useQuery({
    queryKey: upgradeKey,
    queryFn: fetchUpgrade,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const targets = upgrade.data?.targets ?? []
  const limit = useMonitoringLimit(targets[0]?.name ?? '', targets.length > 0)
  const dismiss = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/init/monitoring-upgrade/dismiss', {
        method: 'POST',
      })
      if (!response.ok)
        throw new Error("Couldn't dismiss the monitoring invitation")
    },
    onSuccess: () => client.setQueryData(upgradeKey, { targets: [] }),
  })

  if (!targets.length || (pathname !== '/' && pathname !== '/database'))
    return null

  return (
    <section
      aria-label="Monitoring is new"
      className="mb-6 space-y-4 rounded-card border border-border-primary-soft bg-surface-layout-1 p-5"
    >
      <Text as="h2" level="label-large">
        Monitoring is new.
      </Text>
      <Text as="p" level="body-small">
        RDST can watch your databases in the background, file problems in your
        Inbox, and deep analyze the busiest queries. Your saved queries and
        analyses carry over: once a database is monitored, they show up next to
        the queries RDST is watching.
      </Text>
      {limit.data?.signed_in === false ? (
        <Text as="p" level="body-small" className="text-content-layout-2">
          {SIGNED_OUT_LINE} Choose Turn on to sign in and review your options.
        </Text>
      ) : null}
      <div className="space-y-2">
        {targets.map((target) => (
          <div
            key={target.name}
            className="flex items-center justify-between gap-4 rounded-panel border border-border-layout-1 p-3"
          >
            <Text as="p" level="body-small">
              {target.name} uses{' '}
              {target.engine === 'postgresql'
                ? 'PostgreSQL'
                : target.engine === 'mysql'
                  ? 'MySQL'
                  : target.engine}
              .
            </Text>
            <Button
              label="Turn on"
              modifier="outline"
              onClick={() => setSelected(target.name)}
            />
          </div>
        ))}
      </div>
      <Text as="p" level="caption" className="text-content-layout-2">
        You can turn on monitoring later from each database's Database page.
      </Text>
      {dismiss.isError ? (
        <Alert
          variant="negative"
          label="Couldn't save your choice. Try again."
        />
      ) : null}
      <Button
        label="Not now"
        modifier="ghost"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate()}
      />
      {selected ? (
        <EnableMonitoringDialog
          target={selected}
          open
          onClose={() => setSelected(null)}
        />
      ) : null}
    </section>
  )
}
