import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'

function readOnlySql(engine: string, database?: string): string {
  const databaseName = database || 'your_database'
  if (engine === 'mysql') {
    const escapedDatabase = databaseName.replace(/`/g, '``')
    return `CREATE USER 'rdst_readonly'@'%' IDENTIFIED BY 'replace_with_strong_password';
GRANT SELECT ON \`${escapedDatabase}\`.* TO 'rdst_readonly'@'%';

-- Optional for instance-wide RDST diagnostics. PROCESS exposes other sessions;
-- performance_schema SELECT exposes statement statistics without write access.
GRANT PROCESS ON *.* TO 'rdst_readonly'@'%';
GRANT SELECT ON performance_schema.* TO 'rdst_readonly'@'%';`
  }
  const escapedDatabase = databaseName.replace(/"/g, '""')
  return `CREATE ROLE rdst_readonly LOGIN PASSWORD 'replace_with_strong_password';
GRANT CONNECT ON DATABASE "${escapedDatabase}" TO rdst_readonly;
GRANT USAGE ON SCHEMA public TO rdst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rdst_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO rdst_readonly;`
}

export function WritePrivilegesNotice({
  engine,
  database,
  evidence,
}: {
  engine: string
  database?: string
  evidence?: string
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [sqlOpen, setSqlOpen] = useState(false)
  const sql = readOnlySql(engine, database)

  useEffect(() => {
    setAcknowledged(false)
    setSqlOpen(false)
  }, [engine, database, evidence])

  if (acknowledged) return null

  return (
    <>
      <VStack className="gap-2 items-stretch rounded-lg border border-border-warning-soft bg-surface-warning-soft/10 p-3">
        <Text level="label-small" className="text-content-warning-soft">
          This user has write privileges.
        </Text>
        <Text level="body-small" className="text-content-layout-2">
          Use a read-only database user.
        </Text>
        {evidence && (
          <Text level="caption" className="text-content-layout-3">
            {evidence}
          </Text>
        )}
        <HStack className="gap-2 items-center flex-wrap">
          <Button
            variant="primary"
            modifier="solid"
            size="small"
            label="Proceed anyway"
            onClick={() => setAcknowledged(true)}
          />
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="How to create a read-only user"
            onClick={() => setSqlOpen(true)}
          />
        </HStack>
      </VStack>

      <Modal open={sqlOpen} onOpenChange={setSqlOpen}>
        <ModalContentContainer open={sqlOpen}>
          <ModalContent size="base" className="gap-4">
            <ModalTitle>How to create a read-only user</ModalTitle>
            <ModalDescription>
              Review the database and schema names, replace the password, then
              run this SQL as an administrator.
            </ModalDescription>
            <div className="relative rounded-lg border border-border-layout-1 bg-surface-layout-2 p-3 pr-10">
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-content-layout-2">
                {sql}
              </pre>
              <div className="absolute right-2 top-2">
                <CopyButton text={sql} />
              </div>
            </div>
            <HStack className="justify-end">
              <Button
                variant="primary"
                modifier="solid"
                label="Close"
                onClick={() => setSqlOpen(false)}
              />
            </HStack>
          </ModalContent>
        </ModalContentContainer>
      </Modal>
    </>
  )
}
