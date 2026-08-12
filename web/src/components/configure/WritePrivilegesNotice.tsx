import { Button } from '@rs/ui-new/button'
import { Show } from '@rs/ui-new/show'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'

import { SQLDisplay } from '../SQLDisplay'

export function readOnlySql(engine: string, database?: string) {
  const databaseName = database || 'your_database'
  if (engine === 'mysql') {
    const escapedDatabase = databaseName.replace(/`/g, '``')
    return `CREATE USER 'rdst_readonly'@'replace_with_rdst_client_host'
  IDENTIFIED BY 'replace_with_strong_password'
  WITH MAX_USER_CONNECTIONS 20;

GRANT SELECT, SHOW VIEW ON \`${escapedDatabase}\`.*
  TO 'rdst_readonly'@'replace_with_rdst_client_host';

-- Top and Audit workload visibility:
GRANT PROCESS ON *.*
  TO 'rdst_readonly'@'replace_with_rdst_client_host';
GRANT SELECT ON performance_schema.*
  TO 'rdst_readonly'@'replace_with_rdst_client_host';

-- Optional replication metrics:
-- GRANT REPLICATION CLIENT ON *.*
--   TO 'rdst_readonly'@'replace_with_rdst_client_host';

-- Optional slow-log analysis:
-- GRANT SELECT ON mysql.slow_log
--   TO 'rdst_readonly'@'replace_with_rdst_client_host';`
  }
  const escapedDatabase = databaseName.replace(/"/g, '""')
  return `-- Run while connected to "${escapedDatabase}".
CREATE ROLE rdst_readonly LOGIN
  PASSWORD 'replace_with_strong_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 20;

ALTER ROLE rdst_readonly IN DATABASE "${escapedDatabase}"
  SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE "${escapedDatabase}" TO rdst_readonly;
GRANT USAGE ON SCHEMA public TO rdst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rdst_readonly;
GRANT pg_read_all_stats TO rdst_readonly; -- Top/Audit

-- Optional: pg_monitor adds settings and table-scan statistics.
-- GRANT pg_monitor TO rdst_readonly;`
}

export function WritePrivilegesNotice({
  engine,
  database,
}: {
  engine: string
  database?: string
}) {
  const sql = readOnlySql(engine, database)
  const [showSetup, setShowSetup] = useState(false)

  return (
    <VStack className="gap-2 items-stretch rounded-lg border border-border-layout-1 bg-surface-layout-2/50 p-3">
      <Text level="label-small" className="text-content-layout-1">
        Read-only access highly recommended
      </Text>
      <Text level="body-small" className="text-content-layout-2">
        This account has write access. RDST works with it, but a read-only
        account reduces the risk of unintended database changes.
      </Text>
      <Button
        type="button"
        label={showSetup ? 'Hide read-only setup' : 'View read-only setup'}
        icon={showSetup ? 'chevron-up' : 'chevron-down'}
        iconPosition="right"
        variant="primary"
        modifier="ghost"
        size="small"
        onClick={() => setShowSetup((shown) => !shown)}
        classMerge="self-start"
        aria-expanded={showSetup}
      />
      <Show when={showSetup}>
        <VStack className="gap-3 items-stretch border-t border-border-layout-1 pt-3">
          <VStack className="gap-1 items-stretch">
            <Text level="label-small" className="text-content-layout-1">
              Administrator SQL for a read-only user
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              RDST does not execute this script. Review it with your database
              administrator, replace its placeholders, and run it through your
              normal admin channel. Then connect RDST as rdst_readonly.
            </Text>
          </VStack>
          <SQLDisplay
            sql={sql}
            dialect={engine === 'mysql' ? 'mysql' : 'postgresql'}
            className="overflow-hidden rounded-lg border border-border-layout-1 bg-surface-layout-2 p-3 pr-12"
            showCopy
          />
          <Text level="caption" className="text-content-layout-3">
            {engine === 'mysql'
              ? 'Views that read another database also need SELECT on their underlying tables. Uncomment only the optional metrics you use; PROCESS and performance_schema expose instance-wide activity.'
              : 'This covers existing public tables only. Grant SELECT separately for future tables and other schemas. Top also requires pg_stat_statements to be enabled; PostgreSQL PUBLIC privileges and row-level security still apply.'}
          </Text>
        </VStack>
      </Show>
    </VStack>
  )
}
