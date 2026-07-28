import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Tag } from '@rs/ui-new/tag'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { setEnvSecret } from '../../lib/api'
import { invalidateTargetQueries } from '../../lib/targetQueries'
import {
  updateFleetTargetCredentials,
  useFleetStatus,
} from '../../lib/useFleet'
import type { FleetMember } from '../../types/fleet'
import { groupTargets } from './TargetGroupView'

interface CredentialValues {
  user: string
  password: string
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

function generatedPasswordEnv(target: FleetMember, index: number): string {
  const normalized = target.name
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return `RDST_${normalized || 'TARGET'}_${index + 1}_PASSWORD`
}

export function CredentialsStep({
  targets,
  keyringAvailable,
  onClose,
  onSaved,
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
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<Record<string, CredentialValues>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [persistenceNote, setPersistenceNote] = useState<string | null>(null)
  const {
    check,
    state: connectivityState,
    results: connectivity,
    error: connectivityError,
  } = useFleetStatus()

  const targetKey = useMemo(
    () => targets.map((target) => target.name).join('\u0000'),
    [targets]
  )

  useEffect(() => {
    setValues(
      Object.fromEntries(
        targets.map((target) => [
          target.name,
          { user: target.user || '', password: '' },
        ])
      )
    )
    setSaving(false)
    setSaved(false)
    setError(null)
    setPersistenceNote(null)
  }, [targetKey])

  // Cluster members import together and read best presented together, the
  // same grouping the fleet table uses.
  const groupedTargets = useMemo(
    () => groupTargets(targets, (target) => target.group ?? null),
    [targets]
  )

  const passwordMissing = targets.some(
    (target) => !values[target.name]?.password
  )

  const save = async () => {
    if (targets.length === 0 || passwordMissing) return
    setSaving(true)
    setSaved(false)
    setError(null)
    setPersistenceNote(null)

    try {
      let sessionOnlyMessage: string | null = null
      const passwordEnvCounts = new Map<string, number>()
      for (const target of targets) {
        if (target.password_env) {
          passwordEnvCounts.set(
            target.password_env,
            (passwordEnvCounts.get(target.password_env) ?? 0) + 1
          )
        }
      }

      for (const [index, target] of targets.entries()) {
        const passwordEnv =
          !target.password_env ||
          (passwordEnvCounts.get(target.password_env) ?? 0) > 1
            ? generatedPasswordEnv(target, index)
            : target.password_env

        const nextUser = values[target.name]?.user.trim() || target.user || ''
        const password = values[target.name]?.password || ''

        if (nextUser !== target.user || passwordEnv !== target.password_env) {
          await updateFleetTargetCredentials(target, nextUser, passwordEnv)
        }
        const result = await setEnvSecret({
          name: passwordEnv,
          value: password,
          persist: true,
        })
        if (!result.success) {
          throw new Error(result.message || `Could not save ${target.name}`)
        }
        if (result.session_only) {
          sessionOnlyMessage =
            result.message || 'Credentials are available for this session only.'
        }
      }

      setPersistenceNote(sessionOnlyMessage)
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
  const canFinish = allConnected && !working

  return (
    <VStack className="gap-5 items-stretch" data-testid="credentials-step">
      <VStack className="gap-1 items-stretch">
        <Text level="headline-4" className="text-content-layout-1">
          Set credentials
        </Text>
        <Text level="body-small" className="text-content-layout-3">
          Usernames come from the import — edit them if needed, add each
          password, and you&apos;re done.
        </Text>
      </VStack>

      <VStack className="gap-4 items-stretch">
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
                      <Text level="label-small" className="text-content-layout-1">
                        {target.name}
                      </Text>
                      <Tag
                        size="small"
                        variant="neutral"
                        modifier="ghost"
                        label={ENGINE_LABELS[target.engine] ?? target.engine}
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
                        name={result.status === 'ok' ? 'tick' : result.status === 'checking' ? 'observe' : 'alert'}
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
                    <Text level="caption" className="text-content-layout-3 mb-1 block">
                      Username
                    </Text>
                    <BaseInputText
                      name={`credentials-user-${target.name}`}
                      value={values[target.name]?.user || ''}
                      onChange={(event) =>
                        setValues((current) => ({
                          ...current,
                          [target.name]: {
                            ...current[target.name],
                            user: event.target.value,
                            password: current[target.name]?.password || '',
                          },
                        }))
                      }
                      disabled={working}
                    />
                  </div>
                  <div>
                    <Text level="caption" className="text-content-layout-3 mb-1 block">
                      Password
                    </Text>
                    <BaseInputText
                      name={`credentials-password-${target.name}`}
                      type="password"
                      value={values[target.name]?.password || ''}
                      onChange={(event) =>
                        setValues((current) => ({
                          ...current,
                          [target.name]: {
                            ...current[target.name],
                            user: current[target.name]?.user || target.user || '',
                            password: event.target.value,
                          },
                        }))
                      }
                      autoComplete="new-password"
                      disabled={working}
                    />
                  </div>
                </div>

                {result?.status === 'failed' && result.error && (
                  <Text level="caption" className="text-content-negative-soft">
                    {result.code === 'TARGET_PASSWORD_REQUIRED' ||
                    /environment variable|password_env|export\s+|password not available/i.test(
                      result.error
                    )
                      ? `Enter the password for '${target.name}' again.`
                      : result.error.split('\n')[0]}
                  </Text>
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
        <Alert variant="negative" modifier="outline" label={connectivityError} />
      )}
      {(persistenceNote || !keyringAvailable) && (
        <Text level="caption" className="text-content-layout-3">
          {persistenceNote ||
            'No secure keychain on this machine — passwords apply to this ' +
              'RDST session and can be re-entered later.'}
        </Text>
      )}
      {saved && keyringAvailable && !persistenceNote && (
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
            label={connectivityState === 'running' ? 'Checking connectivity' : 'Save and check'}
            icon="connect"
            iconPosition="right"
            loading={working}
            disabled={working || passwordMissing || targets.length === 0}
            onClick={() => void save()}
          />
        )}
      </HStack>
    </VStack>
  )
}
