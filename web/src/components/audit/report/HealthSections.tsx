import { InlineNotice } from '@rs/ui-new/error-state'
import { VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  formatBytes,
  formatPercent,
  formatReportNumber,
  recordValue,
  reportStatusLabel,
  severityVariant,
} from '../../../lib/auditReportFormat'
import type {
  AuditReport,
  HealthAnalysis,
  WorkloadIndexRecommendation,
} from '../../../types/audit'
import { IndexRecommendationView } from './QueriesSection'
import {
  DetailDisclosure,
  FindingsList,
  SectionCard,
  SimpleTable,
  StatCard,
} from './ReportPrimitives'

/**
 * SECONDARY of the report view: the AI findings + recommended actions. Only
 * renders when a credential resolved (the score itself lives in the verdict
 * hero, so it is not repeated here).
 */
export function HealthDetailSection({ health }: { health: HealthAnalysis }) {
  return (
    <SectionCard icon="document-validation" title="AI Analysis">
      <div className="p-5">
        <VStack className="gap-4 items-start min-w-0">
          {(health.findings?.length || 0) > 0 && (
            <FindingsList findings={health.findings!} />
          )}
        </VStack>
        {(health.query_commentary?.length || 0) > 0 && (
          <div className="mt-5 pt-5 border-t border-border-layout-1">
            <Text
              level="overline"
              className="text-content-layout-3 uppercase block mb-3"
            >
              Query Commentary
            </Text>
            <VStack className="gap-2 items-stretch">
              {health.query_commentary!.map((item, index) => (
                <Text
                  key={index}
                  level="body-small"
                  className="text-content-layout-2"
                >
                  {item.query_hash ? `${item.query_hash}: ` : ''}
                  {item.observation}
                </Text>
              ))}
            </VStack>
          </div>
        )}
      </div>
    </SectionCard>
  )
}

export function HealthReportSections({
  report,
  indexRecommendations = [],
  querySectionId,
  onFocusQuery,
  content,
}: {
  report: AuditReport
  indexRecommendations?: WorkloadIndexRecommendation[]
  querySectionId?: string
  onFocusQuery?: (hash: string) => void
  content: 'cpu' | 'details'
}) {
  const health = report.health_report
  const cpu = report.cloudwatch_cpu
  if (!health && !cpu && indexRecommendations.length === 0) return null
  const vacuum = health?.vacuum_bloat as
    | Record<string, unknown>
    | null
    | undefined
  const indexes = health?.index_health as
    | Record<string, unknown>
    | null
    | undefined
  const connections = health?.connections as
    | Record<string, unknown>
    | null
    | undefined
  const config = health?.config_audit as
    | Record<string, unknown>
    | null
    | undefined
  const replication = health?.replication as
    | Record<string, unknown>
    | null
    | undefined
  const rows = (value: unknown) =>
    Array.isArray(value) ? (value as Record<string, unknown>[]) : []
  return (
    <VStack className="gap-4 items-stretch">
      {content !== 'details' && cpu && (
        <SectionCard icon="observe" title="CloudWatch CPU">
          <div className="p-4 grid grid-cols-2 desktop:grid-cols-4 gap-3">
            <StatCard
              compact
              label="Average CPU"
              value={formatPercent(cpu.avg_cpu)}
            />
            <StatCard
              compact
              label="Maximum CPU"
              value={formatPercent(cpu.max_cpu)}
            />
            <StatCard
              compact
              label="Minimum CPU"
              value={formatPercent(cpu.min_cpu)}
            />
            <StatCard
              compact
              label="Window"
              value={
                cpu.hours != null
                  ? `${formatReportNumber(cpu.hours)} hours`
                  : '-'
              }
            />
          </div>
        </SectionCard>
      )}
      {content !== 'cpu' && vacuum && (
        <SectionCard icon="database" title="Vacuum & Bloat">
          <div className="p-4 grid grid-cols-2 desktop:grid-cols-4 gap-3">
            <StatCard
              compact
              label="Status"
              value={reportStatusLabel(vacuum.summary_status)}
            />
            <StatCard
              compact
              label="TXID Age"
              value={recordValue(vacuum, 'txid_age')}
              hint={
                vacuum.txid_age_pct != null
                  ? `${formatReportNumber(Number(vacuum.txid_age_pct))}% of wraparound limit`
                  : undefined
              }
            />
            <StatCard
              compact
              label="Autovacuum"
              value={recordValue(vacuum, 'autovacuum_enabled')}
            />
            <StatCard
              compact
              label="Workers"
              value={`${recordValue(vacuum, 'autovacuum_workers_active')} / ${recordValue(vacuum, 'autovacuum_max_workers')}`}
            />
          </div>
          {rows(vacuum.tables).length > 0 && (
            <DetailDisclosure
              label="Table statistics"
              count={rows(vacuum.tables).length}
            >
              <SimpleTable
                rows={rows(vacuum.tables)}
                columns={[
                  {
                    key: 'table',
                    label: 'Table',
                    render: (row) =>
                      `${recordValue(row, 'schema')}.${recordValue(row, 'table')}`,
                  },
                  { key: 'live_tuples', label: 'Live tuples' },
                  { key: 'dead_tuples', label: 'Dead tuples' },
                  {
                    key: 'dead_ratio_pct',
                    label: 'Dead %',
                    render: (row) =>
                      typeof row.dead_ratio_pct === 'number'
                        ? formatPercent(row.dead_ratio_pct)
                        : '-',
                  },
                  {
                    key: 'size_bytes',
                    label: 'Size',
                    render: (row) => formatBytes(row.size_bytes),
                  },
                  { key: 'last_autovacuum', label: 'Last vacuum' },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (row) => (
                      <Tag
                        size="small"
                        variant={severityVariant(String(row.status ?? 'info'))}
                        modifier="ghost"
                        label={reportStatusLabel(row.status)}
                      />
                    ),
                  },
                ]}
              />
            </DetailDisclosure>
          )}
        </SectionCard>
      )}
      {content !== 'cpu' && (indexes || indexRecommendations.length > 0) && (
        <SectionCard icon="adjustment-horizontal" title="Indexes">
          <div className="px-5 py-3">
            <Text level="body-small" className="text-content-layout-2">
              {indexes
                ? `${recordValue(indexes, 'total_indexes')} indexes · ${rows(indexes.unused_indexes).length} unused · ${rows(indexes.duplicates).length} duplicates`
                : 'Index health statistics unavailable'}
            </Text>
          </div>
          {indexes && rows(indexes.unused_indexes).length > 0 && (
            <DetailDisclosure
              label="Unused indexes"
              count={rows(indexes.unused_indexes).length}
            >
              <SimpleTable
                rows={rows(indexes.unused_indexes)}
                columns={[
                  { key: 'index', label: 'Unused index' },
                  { key: 'table', label: 'Table' },
                  { key: 'columns', label: 'Columns' },
                  { key: 'scans', label: 'Scans' },
                  {
                    key: 'size_bytes',
                    label: 'Size',
                    render: (row) => formatBytes(row.size_bytes),
                  },
                ]}
              />
            </DetailDisclosure>
          )}
          {indexes && rows(indexes.duplicates).length > 0 && (
            <DetailDisclosure
              label="Duplicate indexes"
              count={rows(indexes.duplicates).length}
            >
              <SimpleTable
                rows={rows(indexes.duplicates)}
                columns={[
                  { key: 'redundant_index', label: 'Redundant index' },
                  { key: 'covered_by', label: 'Covered by' },
                  { key: 'table', label: 'Table' },
                  {
                    key: 'wasted_bytes',
                    label: 'Wasted',
                    render: (row) => formatBytes(row.wasted_bytes),
                  },
                ]}
              />
            </DetailDisclosure>
          )}
          {indexRecommendations.length > 0 && (
            <div className="border-t border-border-layout-1 p-5">
              <VStack className="gap-4 items-stretch">
                {indexRecommendations.map((rec, index) => (
                  <IndexRecommendationView
                    key={index}
                    rec={rec}
                    index={index}
                    querySectionId={querySectionId}
                    onFocusQuery={onFocusQuery}
                  />
                ))}
              </VStack>
            </div>
          )}
        </SectionCard>
      )}
      {content !== 'cpu' && connections && (
        <SectionCard icon="database" title="Connection Detail">
          <div className="p-4 grid grid-cols-2 desktop:grid-cols-4 gap-3">
            <StatCard
              compact
              label="Connections"
              value={`${recordValue(connections, 'total_connections')} / ${recordValue(connections, 'max_connections')}`}
            />
            <StatCard
              compact
              label="By state"
              value={
                Object.entries(
                  (connections.by_state as Record<string, unknown>) || {}
                )
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(', ') || '-'
              }
            />
            <StatCard
              compact
              label="Idle in transaction"
              value={recordValue(connections, 'idle_in_transaction_count')}
            />
            <StatCard
              compact
              label="Pooler"
              value={
                connections.pooler_detected
                  ? recordValue(connections, 'pooler_type')
                  : 'Not detected'
              }
            />
          </div>
          {rows(connections.long_running_idle_in_tx).length > 0 && (
            <DetailDisclosure
              label="Long-running idle transactions"
              count={rows(connections.long_running_idle_in_tx).length}
            >
              <SimpleTable
                rows={rows(connections.long_running_idle_in_tx)}
                columns={[
                  { key: 'pid', label: 'PID' },
                  { key: 'state', label: 'State' },
                  { key: 'duration_seconds', label: 'Duration (s)' },
                  { key: 'application_name', label: 'Application' },
                  { key: 'client_addr', label: 'Client' },
                  { key: 'query_preview', label: 'Query' },
                ]}
              />
            </DetailDisclosure>
          )}
        </SectionCard>
      )}
      {content !== 'cpu' && config && (
        <SectionCard icon="adjustment-horizontal" title="Configuration Audit">
          {recordValue(config, 'instance_ram_gb') !== '-' && (
            <div className="p-5">
              <Text level="caption" className="text-content-layout-3">
                Instance: {recordValue(config, 'instance_ram_gb')} GB RAM ·{' '}
                {recordValue(config, 'instance_vcpus')} vCPU
              </Text>
            </div>
          )}
          {rows(config.settings).length > 0 && (
            <DetailDisclosure
              label="Database settings"
              count={rows(config.settings).length}
            >
              <SimpleTable
                rows={rows(config.settings)}
                columns={[
                  { key: 'parameter', label: 'Setting' },
                  { key: 'current', label: 'Current' },
                  { key: 'recommended', label: 'Recommended' },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (row) => {
                      const status = recordValue(row, 'status').toLowerCase()
                      return (
                        <Tag
                          size="small"
                          variant={severityVariant(status)}
                          modifier="ghost"
                          label={status.toUpperCase()}
                        />
                      )
                    },
                  },
                  { key: 'note', label: 'Notes' },
                ]}
              />
            </DetailDisclosure>
          )}
        </SectionCard>
      )}
      {content !== 'cpu' && replication && (
        <SectionCard icon="layers" title="Replication">
          <div className="p-4 grid grid-cols-2 desktop:grid-cols-4 gap-3">
            <StatCard
              compact
              label="Replica"
              value={recordValue(replication, 'is_replica')}
            />
            <StatCard
              compact
              label="Upstream lag"
              value={
                replication.upstream_lag_seconds != null
                  ? `${formatReportNumber(Number(replication.upstream_lag_seconds))}s`
                  : '-'
              }
            />
            <StatCard
              compact
              label="WAL receiver"
              value={recordValue(replication, 'wal_receiver_status')}
            />
            <StatCard
              compact
              label="Inactive slots"
              value={recordValue(replication, 'inactive_slots')}
            />
          </div>
          {rows(replication.peers).length > 0 && (
            <DetailDisclosure
              label="Replication peers"
              count={rows(replication.peers).length}
            >
              <SimpleTable
                rows={rows(replication.peers)}
                columns={[
                  { key: 'application_name', label: 'Peer' },
                  { key: 'client_addr', label: 'Client' },
                  { key: 'state', label: 'State' },
                  { key: 'sync_state', label: 'Sync' },
                  { key: 'replay_lag_ms', label: 'Replay lag (ms)' },
                ]}
              />
            </DetailDisclosure>
          )}
          {rows(replication.slots).length > 0 && (
            <DetailDisclosure
              label="Replication slots"
              count={rows(replication.slots).length}
            >
              <SimpleTable
                rows={rows(replication.slots)}
                columns={[
                  { key: 'slot_name', label: 'Slot' },
                  { key: 'slot_type', label: 'Type' },
                  { key: 'active', label: 'Active' },
                  {
                    key: 'retained_bytes',
                    label: 'Retained',
                    render: (row) => formatBytes(row.retained_bytes),
                  },
                ]}
              />
            </DetailDisclosure>
          )}
        </SectionCard>
      )}
      {content !== 'cpu' && health?.collection_error && (
        <InlineNotice
          errorClass="database"
          title="Health collection incomplete"
          message={health.collection_error}
        />
      )}
      {content !== 'cpu' &&
        health?.section_errors &&
        Object.keys(health.section_errors).length > 0 && (
          <InlineNotice
            errorClass="database"
            title="Health sections unavailable"
            message={Object.entries(health.section_errors)
              .map(([key, value]) => `${key}: ${value}`)
              .join(' · ')}
          />
        )}
    </VStack>
  )
}
