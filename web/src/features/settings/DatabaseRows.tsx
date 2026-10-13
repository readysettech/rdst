import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import { serverVersion } from '../../components/configure/AddDatabaseDialog'
import { useTargetMonitorLines } from '../../components/TargetMonitorStatus'
import type { ConfigureTarget } from '../../types/configure'
import type { FleetConnectivityEvent } from '../../types/fleet'
import { SignInNote, useSignInHeld } from '../monitor-kit/signInHold'
import { StateDot, switcherState } from '../shared/filters/DatabaseFilter'

/** A row's last tunnel test: still running, or what it found. */
export type TunnelTest =
  | { status: 'testing' | 'ok' }
  | { status: 'failed'; message: string }

export interface DatabaseRowsProps {
  targets: ConfigureTarget[]
  /** The list has not been read yet. */
  pending: boolean
  connectivity: Record<string, FleetConnectivityEvent>
  /** Targets the connectivity check covers; undefined while unknown. */
  checkable?: Set<string>
  testingName: string | null
  onTest: (name: string) => void
  onEdit: (name: string) => void
  onSetDefault: (name: string) => void
  onRemove: (name: string) => void
  onSetPassword: (target: ConfigureTarget) => void
  onTestTunnel: (name: string) => void
  /** Tunnel tests run from this page, by database name. */
  tunnelTests?: Record<string, TunnelTest>
}

const ENGINE: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

function connection(target: ConfigureTarget): string {
  const engine = target.engine ? (ENGINE[target.engine] ?? target.engine) : null
  const where = target.host
    ? `${target.host}${target.port ? `:${target.port}` : ''}`
    : null
  return [engine, where].filter(Boolean).join(' at ')
}

const COLUMNS = 'minmax(0,1fr) 180px auto'
// The line under a row that closes it.
const LAST_LINE = 'border-b border-border-layout-2'
const itemClass = 'h-8.5 min-w-0 rounded-md px-2.5 text-body-small'

/** One row per configured database: name, connection, state word, its actions. */
export function DatabaseRows({
  targets,
  pending,
  connectivity,
  checkable,
  testingName,
  onTest,
  onEdit,
  onSetDefault,
  onRemove,
  onSetPassword,
  onTestTunnel,
  tunnelTests = {},
}: DatabaseRowsProps) {
  const [removing, setRemoving] = useState<string | null>(null)
  // Rows tested here say when they connected; the sweep on opening only names failures.
  const [tested, setTested] = useState<ReadonlySet<string>>(new Set())
  const names = targets.map((target) => target.name)
  const lines = useTargetMonitorLines(names, true)
  const signIn = useSignInHeld(names)

  if (pending)
    return (
      <div className="space-y-3 pt-2">
        <Skeleton className="h-6 w-2/3 rounded-md" />
        <Skeleton className="h-6 w-1/2 rounded-md" />
      </div>
    )
  if (targets.length === 0)
    return (
      <Text as="p" level="body-small" className="py-3.5 text-content-layout-3">
        No database yet.
      </Text>
    )

  return (
    <>
      <DividedList columns={COLUMNS}>
        {targets.map((target) => {
          const result = connectivity[target.name]
          const failed =
            Boolean(result) &&
            result.status !== 'ok' &&
            result.status !== 'checking'
          const connected = tested.has(target.name) && result?.status === 'ok'
          const version = connected
            ? serverVersion(result.server_version)
            : null
          const passwordNeeded =
            target.has_password === false ||
            result?.code === 'TARGET_PASSWORD_REQUIRED'
          const state = switcherState(lines.get(target.name))
          const canTest = !checkable || checkable.has(target.name)
          const tunnel = tunnelTests[target.name]
          return (
            <div key={target.name}>
              <ListRow
                className={
                  failed || connected || tunnel ? 'border-b-0' : undefined
                }
              >
                <div className="min-w-0">
                  <Text
                    as="div"
                    level="body-medium"
                    className="truncate font-medium text-content-layout-1"
                  >
                    {target.name}
                  </Text>
                  <div className="mt-0.5 flex min-w-0 items-center gap-2 text-caption text-content-layout-3">
                    <span className="truncate">{connection(target)}</span>
                    {target.is_default && (
                      <span title="Used when no database is chosen">
                        default
                      </span>
                    )}
                    {passwordNeeded && <span>Password needed</span>}
                  </div>
                  {signIn.has(target.name) && <SignInNote className="mt-0.5" />}
                </div>
                <div className="flex items-center gap-2 text-body-small text-content-layout-2">
                  {state && (
                    <>
                      <StateDot tone={state.tone} />
                      {state.word}
                    </>
                  )}
                </div>
                <div className="flex items-center justify-end gap-1">
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    label="Test connection"
                    loading={testingName === target.name}
                    disabled={!canTest}
                    onClick={() => {
                      setTested((names) => new Set(names).add(target.name))
                      onTest(target.name)
                    }}
                  />
                  <Dropdown>
                    <Dropdown.Trigger asChild>
                      <Pressable
                        aria-label={`More actions for ${target.name}`}
                        className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-control text-content-layout-3 hover:bg-surface-layout-2 hover:text-content-layout-1 data-[state=open]:bg-surface-layout-2"
                      >
                        <Icon name="more" label="" size="small" />
                      </Pressable>
                    </Dropdown.Trigger>
                    <Dropdown.Content
                      align="end"
                      className="w-50 min-w-50 rounded-panel bg-surface-primary-soft p-1.5"
                    >
                      <Dropdown.Item
                        label="Set default"
                        className={itemClass}
                        disabled={Boolean(target.is_default)}
                        onClick={() => onSetDefault(target.name)}
                      />
                      <Dropdown.Item
                        label="Edit"
                        className={itemClass}
                        onClick={() => onEdit(target.name)}
                      />
                      {target.ssh && (
                        <Dropdown.Item
                          label="Test tunnel"
                          className={itemClass}
                          disabled={tunnel?.status === 'testing'}
                          onClick={() => onTestTunnel(target.name)}
                        />
                      )}
                      <Dropdown.Separator className="-mx-1.5" />
                      <Dropdown.Item
                        label="Remove"
                        className={itemClass}
                        onClick={() => setRemoving(target.name)}
                      />
                    </Dropdown.Content>
                  </Dropdown>
                </div>
              </ListRow>
              {connected && (
                <output
                  className={tunnel ? 'block pb-2' : `block pb-3 ${LAST_LINE}`}
                >
                  <Text
                    as="p"
                    level="caption"
                    className="text-content-positive-soft"
                  >
                    {version ? `Connected to ${version}.` : 'Connected.'}
                  </Text>
                </output>
              )}
              {tunnel && <TunnelLine test={tunnel} last={!failed} />}
              {failed && (
                <div className="flex items-center gap-3 border-b border-border-layout-2 pb-3">
                  <div className="min-w-0 flex-1">
                    <Alert
                      variant="warning"
                      modifier="outline"
                      label={result.error ?? "Can't reach the database"}
                    />
                  </div>
                  {passwordNeeded && (
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Set password"
                      onClick={() => onSetPassword(target)}
                    />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </DividedList>
      <ConfirmDialog
        tone="quiet"
        isOpen={removing !== null}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          if (removing) onRemove(removing)
          setRemoving(null)
        }}
        title={`Remove ${removing ?? ''}?`}
        notice={{
          accent: 'negative',
          icon: 'alert',
          message:
            'RDST forgets the connection and deletes its readings, Inbox items, actions and reports. Adding it back starts fresh.',
        }}
        confirmLabel="Remove"
        confirmVariant="negative"
      />
    </>
  )
}

/** Under the row, what Test tunnel found, the way Test connection says it. */
function TunnelLine({ test, last }: { test: TunnelTest; last: boolean }) {
  const className = last ? `pb-3 ${LAST_LINE}` : 'pb-2'
  if (test.status === 'failed')
    return (
      <div className={className}>
        <Alert variant="warning" modifier="outline" label={test.message} />
      </div>
    )
  return (
    <output className={`block ${className}`}>
      <Text
        as="p"
        level="caption"
        className={
          test.status === 'ok'
            ? 'text-content-positive-soft'
            : 'text-content-layout-3'
        }
      >
        {test.status === 'ok'
          ? 'Tunnel works. The jump host and the database are reachable.'
          : 'Testing the tunnel...'}
      </Text>
    </output>
  )
}
