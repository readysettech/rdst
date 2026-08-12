import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { isPrivateConnectivityFailure } from '../../lib/sshErrors'
import { invalidateTargetQueries } from '../../lib/targetQueries'
import {
  updateFleetTargetCredentials,
  useFleetStatus,
} from '../../lib/useFleet'
import type { FleetMember } from '../../types/fleet'
import { ConnectionFailureActions } from '../ConnectionFailureActions'
import type { PrivateTargetGroup } from './privateTargets'
import {
  assembleSshConfig,
  SshFields,
  type SshFieldsValue,
  sshFieldsValue,
} from './SshFields'
import { groupTargets } from './TargetGroupView'
import { WritePrivilegesNotice } from './WritePrivilegesNotice'

interface CredentialValues {
  user: string
  password: string
  database: string
}

const ENGINE_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

function roleOf(target: FleetMember): string | null {
  for (const tag of target.tags ?? []) {
    if (tag.startsWith('role:')) return tag.slice('role:'.length)
  }
  return null
}

export function CredentialsStep({
  targets,
  keyringAvailable,
  onClose,
  onSaved,
  privateTargetGroups = [],
}: {
  targets: FleetMember[]
  keyringAvailable: boolean
  onClose: () => void
  /**
   * The passwords reached the config for these targets. Fires on a successful
   * save, ahead of the connectivity verdict, so a wrong-but-stored password
   * still refreshes whatever the caller shows for those rows.
   */
  onSaved?: (names: string[]) => void | Promise<void>
  privateTargetGroups?: PrivateTargetGroup[]
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<Record<string, CredentialValues>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [batchCredentials, setBatchCredentials] = useState({
    user: '',
    password: '',
  })
  const [sshValues, setSshValues] = useState<Record<string, SshFieldsValue>>({})
  const editedSshTargets = useRef(new Set<string>())
  const {
    check,
    state: connectivityState,
    results: connectivity,
    error: connectivityError,
  } = useFleetStatus()

  const targetKey = useMemo(
    () =>
      [
        ...targets.map((target) => target.name),
        ...privateTargetGroups.map(
          (group) => `${group.key}:${group.targetNames.join(',')}`
        ),
      ].join('\u0000'),
    [targets, privateTargetGroups]
  )

  useEffect(() => {
    setValues(
      Object.fromEntries(
        targets.map((target) => [
          target.name,
          {
            user: target.user || '',
            password: '',
            database: target.database || '',
          },
        ])
      )
    )
    setSaving(false)
    setSaved(false)
    setError(null)
    editedSshTargets.current.clear()
    setBatchCredentials({ user: '', password: '' })
    setSshValues(
      Object.fromEntries(
        privateTargetGroups.flatMap((group) =>
          group.targetNames.map((targetName) => [targetName, sshFieldsValue()])
        )
      )
    )
  }, [targetKey])

  // Cluster members import together and read best presented together, the
  // same grouping the fleet table uses.
  const groupedTargets = useMemo(
    () => groupTargets(targets, (target) => target.group ?? null),
    [targets]
  )

  const effectivePrivateGroups = useMemo(() => {
    const targetNames = new Set(targets.map((target) => target.name))
    const declared = privateTargetGroups
      .map((group) => ({
        ...group,
        targetNames: group.targetNames.filter((name) => targetNames.has(name)),
      }))
      .filter((group) => group.targetNames.length > 0)
    const alreadyGrouped = new Set(
      declared.flatMap((group) => group.targetNames)
    )
    const diagnosed = targets
      .filter(
        (target) =>
          !alreadyGrouped.has(target.name) &&
          connectivity[target.name]?.status === 'failed' &&
          isPrivateConnectivityFailure(connectivity[target.name])
      )
      .map((target) => target.name)

    return diagnosed.length > 0
      ? [
          ...declared,
          {
            key: 'connection-test-private',
            label: 'Connection test',
            targetNames: diagnosed,
          },
        ]
      : declared
  }, [targets, privateTargetGroups, connectivity])

  const privateTargetCount = new Set(
    effectivePrivateGroups.flatMap((group) => group.targetNames)
  ).size

  const privateTargetNames = useMemo(
    () => [
      ...new Set(effectivePrivateGroups.flatMap((group) => group.targetNames)),
    ],
    [effectivePrivateGroups]
  )

  const updateSsh = (targetName: string, next: SshFieldsValue) => {
    editedSshTargets.current.add(targetName)
    const index = privateTargetNames.indexOf(targetName)
    setSshValues((current) => {
      const updated = { ...current, [targetName]: { ...next } }
      for (const laterTarget of privateTargetNames.slice(index + 1)) {
        if (!editedSshTargets.current.has(laterTarget)) {
          updated[laterTarget] = { ...next }
        }
      }
      return updated
    })
  }

  const passwordMissing = targets.some(
    (target) => !target.password_secret_arn && !values[target.name]?.password
  )
  const databaseMissing = targets.some(
    (target) => !values[target.name]?.database.trim()
  )
  const sshMissing = privateTargetNames.some(
    (targetName) =>
      !assembleSshConfig(sshValues[targetName] ?? sshFieldsValue())
  )

  const save = async () => {
    if (
      targets.length === 0 ||
      passwordMissing ||
      databaseMissing ||
      sshMissing
    )
      return
    setSaving(true)
    setSaved(false)
    setError(null)

    try {
      for (const target of targets) {
        const nextUser = values[target.name]?.user.trim() || target.user || ''
        const database = values[target.name]?.database.trim() || ''
        const password = values[target.name]?.password || ''
        const ssh = privateTargetNames.includes(target.name)
          ? assembleSshConfig(sshValues[target.name] ?? sshFieldsValue())
          : undefined

        await updateFleetTargetCredentials(
          target,
          nextUser,
          target.password_secret_arn ? '' : password,
          database,
          ssh
        )
      }

      await invalidateTargetQueries(queryClient)
      setSaved(true)
      setSaving(false)
      const savedNames = targets.map((target) => target.name)
      await onSaved?.(savedNames)
      await check(undefined, savedNames)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setSaving(false)
    }
  }

  const working = saving || connectivityState === 'running'
  // Saving the secret always succeeds; the connectivity check is what tells us
  // the password is right. Only offer Done once every target actually
  // connected, so a wrong password leaves "Save and check" in place to retry
  // rather than forcing the user out.
  const allConnected =
    saved &&
    targets.length > 0 &&
    targets.every((target) => connectivity[target.name]?.status === 'ok')
  const writableTargets = targets.filter(
    (target) =>
      connectivity[target.name]?.status === 'ok' &&
      connectivity[target.name]?.privileges?.writable === true
  )
  const canFinish = allConnected && !working

  return (
    <VStack className="gap-5 items-stretch" data-testid="credentials-step">
      <VStack className="gap-1 items-stretch">
        <Text level="headline-4" className="text-content-layout-1">
          Set credentials
        </Text>
        <Text level="body-small" className="text-content-layout-3">
          Review each username and database, then enter the passwords.
        </Text>
      </VStack>

      {targets.length > 1 && (
        <VStack className="gap-3 items-stretch rounded-xl border border-border-layout-1 bg-surface-layout-2/30 p-4">
          <Text level="label-small" className="text-content-layout-1">
            Apply these credentials to all selected databases
          </Text>
          <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3">
            <BaseInputText
              name="batch-credentials-user"
              value={batchCredentials.user}
              onChange={(event) =>
                setBatchCredentials((current) => ({
                  ...current,
                  user: event.target.value,
                }))
              }
              placeholder="Monitoring username"
              disabled={working}
            />
            <BaseInputText
              name="batch-credentials-password"
              type="password"
              value={batchCredentials.password}
              onChange={(event) =>
                setBatchCredentials((current) => ({
                  ...current,
                  password: event.target.value,
                }))
              }
              placeholder="Database password"
              autoComplete="new-password"
              disabled={working}
            />
          </div>
          <HStack className="justify-end">
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              label="Apply to all"
              disabled={
                working ||
                !batchCredentials.user.trim() ||
                !batchCredentials.password
              }
              onClick={() => {
                setValues((current) =>
                  Object.fromEntries(
                    targets.map((target) => [
                      target.name,
                      {
                        ...current[target.name],
                        user: batchCredentials.user,
                        password: target.password_secret_arn
                          ? current[target.name]?.password || ''
                          : batchCredentials.password,
                      },
                    ])
                  )
                )
              }}
            />
          </HStack>
        </VStack>
      )}

      {privateTargetCount > 0 && (
        <VStack className="gap-3 items-stretch">
          <Alert
            variant="warning"
            modifier="outline"
            label={`${privateTargetCount} private ${privateTargetCount === 1 ? 'database requires' : 'databases require'} an SSH jump host`}
          />
          {effectivePrivateGroups.map((group) => (
            <div
              key={group.key}
              className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 p-4"
            >
              <VStack className="gap-3 items-stretch">
                <Text level="label-small" className="text-content-layout-1">
                  {group.label}
                </Text>
                {group.targetNames.map((targetName) => (
                  <div
                    key={targetName}
                    className="rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3"
                  >
                    <VStack className="gap-3 items-stretch">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        {targetName}
                      </Text>
                      <SshFields
                        value={sshValues[targetName] ?? sshFieldsValue()}
                        onChange={(next) => updateSsh(targetName, next)}
                        disabled={saving}
                        idPrefix={`private-${targetName.replace(/[^a-z0-9]+/gi, '-')}`}
                      />
                    </VStack>
                  </div>
                ))}
              </VStack>
            </div>
          ))}
        </VStack>
      )}

      <VStack className="gap-4 items-stretch">
        {writableTargets.length > 0 && (
          <Alert
            variant="informative"
            modifier="outline"
            label={`Read-only access is recommended for ${writableTargets.length} connected ${writableTargets.length === 1 ? 'account' : 'accounts'}.`}
          />
        )}
        {groupedTargets.map(({ group: groupName, targets: members }) => (
          <div
            key={groupName || 'ungrouped'}
            className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4"
          >
            <VStack className="gap-3 items-stretch">
              <HStack className="gap-2.5 items-center flex-wrap border-b border-border-layout-1 pb-3">
                <div className="w-8 h-8 rounded-lg bg-surface-layout-2 flex items-center justify-center shrink-0">
                  <Icon
                    name={groupName ? 'layers' : 'database'}
                    label=""
                    aria-hidden="true"
                    className="w-4 h-4 text-content-layout-2"
                  />
                </div>
                <VStack className="gap-0 items-start">
                  <Text level="label-medium" className="text-content-layout-1">
                    {groupName || 'Ungrouped'}
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    {members.length === 1
                      ? '1 instance'
                      : `${members.length} instances`}
                    {groupName ? ' in this cluster' : ''}
                  </Text>
                </VStack>
              </HStack>
              {members.map((target) => {
                const result = connectivity[target.name]
                return (
                  <div
                    key={target.name}
                    className="rounded-lg border border-border-layout-1 bg-surface-layout-2/40 p-3"
                  >
                    <VStack className="gap-2 items-stretch">
                      <HStack className="justify-between gap-3 items-start">
                        <VStack className="gap-0.5 items-start min-w-0">
                          <HStack className="gap-2 items-center flex-wrap">
                            <Text
                              level="label-small"
                              className="text-content-layout-1"
                            >
                              {target.name}
                            </Text>
                            <Tag
                              size="small"
                              variant="neutral"
                              modifier="ghost"
                              label={
                                ENGINE_LABELS[target.engine] ?? target.engine
                              }
                            />
                            {roleOf(target) && (
                              <Tag
                                size="small"
                                variant="informative"
                                modifier="ghost"
                                label={roleOf(target) as string}
                              />
                            )}
                          </HStack>
                          <Text
                            level="caption"
                            className="text-content-layout-3 truncate max-w-full"
                          >
                            {target.host}
                          </Text>
                        </VStack>
                        {result && (
                          <HStack className="gap-1.5 items-center shrink-0">
                            <Icon
                              name={
                                result.status === 'ok'
                                  ? 'tick'
                                  : result.status === 'checking'
                                    ? 'observe'
                                    : 'alert'
                              }
                              label={result.status}
                              className={`w-4 h-4 ${
                                result.status === 'ok'
                                  ? 'text-content-positive-soft'
                                  : result.status === 'checking'
                                    ? 'text-content-warning-soft'
                                    : 'text-content-negative-soft'
                              }`}
                            />
                            <Text
                              level="caption"
                              className={
                                result.status === 'ok'
                                  ? 'text-content-positive-soft'
                                  : result.status === 'checking'
                                    ? 'text-content-warning-soft'
                                    : 'text-content-negative-soft'
                              }
                            >
                              {result.status === 'ok'
                                ? 'Connected'
                                : result.status === 'checking'
                                  ? 'Checking'
                                  : 'Failed'}
                            </Text>
                          </HStack>
                        )}
                      </HStack>

                      <div className="grid grid-cols-1 laptop:grid-cols-2 gap-3">
                        <div>
                          <Text
                            level="caption"
                            className="text-content-layout-3 mb-1 block"
                          >
                            Database
                          </Text>
                          <BaseInputText
                            name={`credentials-database-${target.name}`}
                            value={values[target.name]?.database || ''}
                            onChange={(event) => {
                              setValues((current) => ({
                                ...current,
                                [target.name]: {
                                  ...current[target.name],
                                  database: event.target.value,
                                },
                              }))
                            }}
                            placeholder="Required database name"
                            disabled={working}
                            required
                          />
                          {!target.database &&
                            ((target.tags ?? []).some((tag) =>
                              tag.startsWith('aws-account:')
                            ) ||
                              target.host.endsWith('.rds.amazonaws.com')) && (
                              <Text
                                level="caption"
                                className="text-content-layout-3 mt-1"
                              >
                                Enter the database name used by this instance.
                              </Text>
                            )}
                        </div>
                        <div>
                          <Text
                            level="caption"
                            className="text-content-layout-3 mb-1 block"
                          >
                            Username
                          </Text>
                          <BaseInputText
                            name={`credentials-user-${target.name}`}
                            value={values[target.name]?.user || ''}
                            onChange={(event) => {
                              setValues((current) => ({
                                ...current,
                                [target.name]: {
                                  ...current[target.name],
                                  user: event.target.value,
                                  password:
                                    current[target.name]?.password || '',
                                  database:
                                    current[target.name]?.database || '',
                                },
                              }))
                            }}
                            disabled={working}
                          />
                        </div>
                        <div>
                          <Text
                            level="caption"
                            className="text-content-layout-3 mb-1 block"
                          >
                            Password
                          </Text>
                          {target.password_secret_arn ? (
                            <Tag
                              size="small"
                              variant="neutral"
                              modifier="ghost"
                              label="Credentials stored in AWS Secrets Manager"
                            />
                          ) : (
                            <BaseInputText
                              name={`credentials-password-${target.name}`}
                              type="password"
                              value={values[target.name]?.password || ''}
                              onChange={(event) => {
                                setValues((current) => ({
                                  ...current,
                                  [target.name]: {
                                    ...current[target.name],
                                    user:
                                      current[target.name]?.user ||
                                      target.user ||
                                      '',
                                    password: event.target.value,
                                    database:
                                      current[target.name]?.database || '',
                                  },
                                }))
                              }}
                              autoComplete="new-password"
                              disabled={working}
                            />
                          )}
                        </div>
                      </div>

                      {result?.status === 'failed' && result.error && (
                        <ConnectionFailureActions
                          failure={{
                            target: target.name,
                            message: result.error,
                            category: result.category,
                            code: result.code,
                          }}
                          passwordRequired={
                            result.code === 'TARGET_PASSWORD_REQUIRED' ||
                            /environment variable|password_env|export\s+|password not available/i.test(
                              result.error
                            )
                          }
                          onRetry={async () => {
                            const checked = await check(undefined, [
                              target.name,
                            ])
                            return checked[target.name]?.status === 'ok'
                          }}
                        />
                      )}
                      {result?.status === 'ok' &&
                        result.privileges?.writable && (
                          <WritePrivilegesNotice
                            engine={target.engine}
                            database={
                              values[target.name]?.database || target.database
                            }
                          />
                        )}
                    </VStack>
                  </div>
                )
              })}
            </VStack>
          </div>
        ))}
      </VStack>

      {targets.length === 0 && (
        <Alert
          variant="warning"
          modifier="outline"
          label="The imported targets are still loading. Try opening this step again."
        />
      )}
      {error && <Alert variant="negative" modifier="outline" label={error} />}
      {connectivityError && (
        <Alert
          variant="negative"
          modifier="outline"
          label={connectivityError}
        />
      )}
      {!keyringAvailable && (
        <Text level="caption" className="text-content-layout-3">
          No OS keychain found. Re-enter the password after RDST restarts.
        </Text>
      )}
      {saved && keyringAvailable && (
        <Text level="caption" className="text-content-positive-soft">
          Saved to your OS keyring.
        </Text>
      )}

      <HStack className="justify-between gap-3 items-center">
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Set up later"
          onClick={onClose}
          disabled={working}
        />
        {canFinish ? (
          <Button
            variant="primary"
            modifier="solid"
            label="Done"
            icon="tick"
            iconPosition="right"
            onClick={onClose}
          />
        ) : (
          <Button
            variant="primary"
            modifier="solid"
            label={
              connectivityState === 'running'
                ? 'Checking connectivity'
                : 'Save and check'
            }
            icon="connect"
            iconPosition="right"
            loading={working}
            disabled={
              working ||
              passwordMissing ||
              databaseMissing ||
              sshMissing ||
              targets.length === 0
            }
            onClick={() => void save()}
          />
        )}
      </HStack>
    </VStack>
  )
}
