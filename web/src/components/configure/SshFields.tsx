import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect, useMemo, useState } from 'react'
import { isDesktopRuntime, selectDesktopSshKey } from '../../lib/desktop'
import {
  fetchSshDirectory,
  fetchSshKeys,
  fetchSshProfiles,
  importSshKey,
  type SshAuthOption,
  type SshBrowserDirectory,
  type SshProfile,
} from '../../lib/tunnels'
import type { SshConfig } from '../../types/configure'
import { FieldLabel } from './FieldLabel'

export interface SshFieldsValue {
  profile: string
  host: string
  port: number
  user: string
  key_path: string
}

export function sshFieldsValue(ssh?: SshConfig | null): SshFieldsValue {
  const profile = ssh && 'profile' in ssh ? ssh.profile : ''
  return {
    profile: profile ?? '',
    host: ssh && 'host' in ssh ? (ssh.host ?? '') : '',
    port: ssh?.port ?? 22,
    user: ssh?.user ?? '',
    key_path: ssh?.key_path ?? '',
  }
}

/** A blank jump host means the optional SSH section should be removed. */
export function assembleSshConfig(
  value: SshFieldsValue
): SshConfig | undefined {
  const host = value.host.trim()
  const profile = value.profile.trim()
  if (profile) return { profile }
  if (!host) return undefined

  return {
    host,
    port: value.port || 22,
    user: value.user.trim() || undefined,
    key_path: value.key_path.trim() || undefined,
  }
}

export function SshFields({
  value,
  onChange,
  disabled,
  idPrefix = 'ssh',
}: {
  value: SshFieldsValue
  onChange: (value: SshFieldsValue) => void
  disabled?: boolean
  idPrefix?: string
}) {
  const [authOptions, setAuthOptions] = useState<SshAuthOption[]>([])
  const [rememberedHosts, setRememberedHosts] = useState<SshProfile[]>([])
  const [hostsLoaded, setHostsLoaded] = useState(false)
  const [hostMode, setHostMode] = useState(
    value.host ? `current:${value.host}` : 'manual'
  )
  const [keyMode, setKeyMode] = useState<'select' | 'manual' | 'browse'>(
    'select'
  )
  const [browserDirectory, setBrowserDirectory] =
    useState<SshBrowserDirectory | null>(null)
  const [browserLoading, setBrowserLoading] = useState(false)
  const [browserError, setBrowserError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const desktop = isDesktopRuntime()
  const selectedProfile = value.profile
    ? rememberedHosts.find((item) => item.name === value.profile)
    : undefined
  const displayValue: SshFieldsValue = selectedProfile
    ? {
        profile: value.profile,
        host: selectedProfile.host,
        port: selectedProfile.port || 22,
        user: selectedProfile.user ?? '',
        key_path: selectedProfile.key_path ?? '',
      }
    : value
  const update = (patch: Partial<SshFieldsValue>) =>
    onChange({ ...displayValue, profile: '', ...patch })

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      void fetchSshKeys(displayValue.host.trim())
        .then((options) => {
          if (!cancelled) setAuthOptions(options)
        })
        .catch(() => {
          if (!cancelled) setAuthOptions([])
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [displayValue.host])

  useEffect(() => {
    let cancelled = false
    void fetchSshProfiles()
      .then((profiles) => {
        if (!cancelled) setRememberedHosts(profiles)
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setHostsLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!selectedProfile) return
    setHostMode(`current:${selectedProfile.host}`)
  }, [selectedProfile])

  const keyOptions = authOptions.filter(
    (option): option is SshAuthOption & { key_path: string } =>
      Boolean(option.key_path)
  )
  const configHosts = authOptions.filter(
    (option): option is SshAuthOption & { host: string } =>
      option.kind === 'host' && Boolean(option.host)
  )
  const isCurrentHost = (host: {
    host: string
    port?: number | null
    user?: string | null
  }) =>
    Boolean(displayValue.host) &&
    host.host === displayValue.host &&
    (host.port || 22) === (displayValue.port || 22) &&
    (host.user ?? '') === displayValue.user
  const hostOptions = [
    ...(displayValue.host
      ? [
          {
            value: `current:${displayValue.host}`,
            label: `${displayValue.host}${displayValue.user ? ` (${displayValue.user})` : ''} — current`,
          },
        ]
      : []),
    ...rememberedHosts
      .filter((host) => !isCurrentHost(host))
      .map((host) => ({
        value: `remembered:${host.name}`,
        label: `${host.host}${host.user ? ` (${host.user})` : ''}`,
      })),
    ...configHosts
      .filter((host) => !isCurrentHost(host))
      .map((host) => ({
        value: `config:${host.host}`,
        label: `${host.label}${host.user ? ` (${host.user})` : ''} — from SSH config`,
      })),
  ]
  // The select's value must always exist in its options, even on renders where
  // hostMode and value.host briefly disagree; otherwise the control goes blank.
  if (
    hostMode !== 'manual' &&
    !hostOptions.some((option) => option.value === hostMode) &&
    hostMode.startsWith('current:')
  ) {
    hostOptions.unshift({
      value: hostMode,
      label: `${hostMode.slice('current:'.length)}${displayValue.user ? ` (${displayValue.user})` : ''}`,
    })
  }

  useEffect(() => {
    if (hostsLoaded && hostOptions.length === 0 && !displayValue.host) {
      setHostMode('manual')
    }
  }, [hostsLoaded, hostOptions.length, displayValue.host])

  const selectedKey = keyOptions.find(
    (option) => option.key_path === displayValue.key_path
  )
  const selectKeyOptions = useMemo(
    () => [
      ...(displayValue.key_path && !selectedKey
        ? [{ value: displayValue.key_path, label: displayValue.key_path }]
        : []),
      ...keyOptions.map((option) => ({
        value: option.key_path,
        label: option.label,
      })),
      ...(!desktop
        ? [{ value: '__browse__', label: 'Browse local files' }]
        : []),
      { value: '__manual__', label: 'Enter path manually' },
    ],
    [keyOptions, selectedKey, displayValue.key_path, desktop]
  )

  const chooseHost = (selection: string) => {
    if (selection === 'manual' || selection.startsWith('current:')) {
      setHostMode(selection)
      return
    }

    const remembered = rememberedHosts.find(
      (host) => `remembered:${host.name}` === selection
    )
    if (remembered) {
      // Updating value.host makes this host the synthetic `current:` option
      // and removes its `remembered:` option. Keep the controlled value in
      // sync with the option that survives that render.
      setHostMode(`current:${remembered.host}`)
      update({
        profile: '',
        host: remembered.host,
        port: remembered.port || 22,
        user: remembered.user ?? '',
      })
      return
    }

    const configHost = configHosts.find(
      (host) => `config:${host.host}` === selection
    )
    if (configHost) {
      setHostMode(`current:${configHost.host}`)
      update({
        profile: '',
        host: configHost.host,
        port: configHost.port ?? 22,
        user: configHost.user ?? '',
      })
    }
  }

  const loadBrowserDirectory = (path?: string) => {
    setBrowserLoading(true)
    setBrowserError(null)
    void fetchSshDirectory(path)
      .then(setBrowserDirectory)
      .catch((caught) =>
        setBrowserError(
          caught instanceof Error ? caught.message : String(caught)
        )
      )
      .finally(() => setBrowserLoading(false))
  }

  const browseForKey = () => {
    setImportError(null)
    void selectDesktopSshKey()
      ?.then((keyPath) => {
        if (keyPath) update({ profile: '', key_path: keyPath })
      })
      .catch((caught) =>
        setImportError(
          caught instanceof Error ? caught.message : String(caught)
        )
      )
  }

  return (
    <VStack className="gap-4 items-stretch">
      {hostOptions.length > 0 && (
        <div>
          <FieldLabel htmlFor={`${idPrefix}-host-choice`}>Jump host</FieldLabel>
          <BaseInputSelect
            id={`${idPrefix}-host-choice`}
            name={`${idPrefix}-host-choice`}
            value={hostMode}
            placeholder="Choose a jump host"
            onValueChange={chooseHost}
            options={[
              ...hostOptions,
              { value: 'manual', label: 'Enter manually' },
            ]}
            disabled={disabled}
          />
        </div>
      )}

      {(hostMode === 'manual' || hostOptions.length === 0) && (
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <div>
            <FieldLabel htmlFor={`${idPrefix}-host`}>Jump host</FieldLabel>
            <BaseInputText
              id={`${idPrefix}-host`}
              name={`${idPrefix}-host`}
              value={displayValue.host}
              onChange={(event) =>
                update({ profile: '', host: event.target.value })
              }
              placeholder="bastion.example.com"
              disabled={disabled}
            />
          </div>

          <div>
            <FieldLabel htmlFor={`${idPrefix}-port`}>SSH port</FieldLabel>
            <BaseInputText
              id={`${idPrefix}-port`}
              name={`${idPrefix}-port`}
              type="number"
              value={String(displayValue.port)}
              onChange={(event) =>
                update({ profile: '', port: Number(event.target.value) || 22 })
              }
              placeholder="22"
              disabled={disabled}
            />
          </div>

          <div>
            <FieldLabel htmlFor={`${idPrefix}-user`}>SSH user</FieldLabel>
            <BaseInputText
              id={`${idPrefix}-user`}
              name={`${idPrefix}-user`}
              value={displayValue.user}
              onChange={(event) =>
                update({ profile: '', user: event.target.value })
              }
              placeholder="ec2-user"
              disabled={disabled}
            />
          </div>
        </div>
      )}

      <div>
        <FieldLabel htmlFor={`${idPrefix}-key-path`}>Key path</FieldLabel>
        {keyMode === 'select' && (
          <VStack className="gap-2 items-stretch">
            <BaseInputSelect
              id={`${idPrefix}-key-path`}
              name={`${idPrefix}-key-path`}
              value={displayValue.key_path}
              placeholder="Choose a private key"
              onValueChange={(selection) => {
                if (selection === '__browse__') {
                  setKeyMode('browse')
                  loadBrowserDirectory()
                  return
                }
                if (selection === '__manual__') {
                  setKeyMode('manual')
                  return
                }
                setImportError(null)
                update({ profile: '', key_path: selection })
              }}
              options={selectKeyOptions}
              disabled={disabled}
            />
            {desktop && (
              <div>
                <Button
                  variant="primary"
                  modifier="outline"
                  size="small"
                  label="Browse..."
                  onClick={browseForKey}
                  disabled={disabled}
                />
              </div>
            )}
          </VStack>
        )}
        {keyMode === 'manual' && (
          <VStack className="gap-2 items-stretch">
            <BaseInputText
              id={`${idPrefix}-key-path`}
              name={`${idPrefix}-key-path`}
              value={displayValue.key_path}
              onChange={(event) =>
                update({ profile: '', key_path: event.target.value })
              }
              placeholder="~/.ssh/id_ed25519"
              disabled={disabled}
            />
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Back to detected keys"
              onClick={() => setKeyMode('select')}
              disabled={disabled}
            />
          </VStack>
        )}
        {keyMode === 'browse' && (
          <VStack className="gap-2 items-stretch">
            {browserDirectory && (
              <Text level="caption" className="text-content-layout-3 break-all">
                {browserDirectory.path}
              </Text>
            )}
            <div className="flex flex-wrap gap-2">
              {browserDirectory?.parent && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  label="Up one folder"
                  onClick={() =>
                    loadBrowserDirectory(browserDirectory.parent ?? undefined)
                  }
                  disabled={disabled || browserLoading}
                />
              )}
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Back to detected keys"
                onClick={() => setKeyMode('select')}
                disabled={disabled || browserLoading}
              />
            </div>
            <VStack className="gap-1 items-stretch max-h-52 overflow-y-auto rounded-lg border border-border-layout-1 p-2">
              {browserDirectory?.entries.map((entry) => (
                <Button
                  key={entry.path}
                  variant="primary"
                  modifier="ghost"
                  size="small"
                  fullWidth
                  label={`${entry.is_dir ? 'Open folder' : 'Select file'}: ${entry.name}`}
                  onClick={() => {
                    if (entry.is_dir) {
                      loadBrowserDirectory(entry.path)
                    } else {
                      update({ profile: '', key_path: entry.path })
                      setKeyMode('select')
                    }
                  }}
                  disabled={disabled || browserLoading}
                />
              ))}
              {!browserLoading && browserDirectory?.entries.length === 0 && (
                <Text level="caption" className="text-content-layout-3">
                  This folder is empty.
                </Text>
              )}
            </VStack>
            {browserLoading && (
              <Text level="caption" className="text-content-layout-3">
                Loading files...
              </Text>
            )}
            {browserError && (
              <Text level="caption" className="text-content-negative-soft">
                {browserError}
              </Text>
            )}
          </VStack>
        )}
      </div>

      {selectedKey?.outside_ssh_dir && (
        <div>
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Copy to ~/.ssh and set permissions"
            loading={importing}
            disabled={disabled || importing}
            onClick={() => {
              setImporting(true)
              setImportError(null)
              void importSshKey(selectedKey.key_path)
                .then((keyPath) => update({ profile: '', key_path: keyPath }))
                .catch((caught) =>
                  setImportError(
                    caught instanceof Error ? caught.message : String(caught)
                  )
                )
                .finally(() => setImporting(false))
            }}
          />
        </div>
      )}
      {importError && (
        <Text level="caption" className="text-content-negative-soft">
          {importError}
        </Text>
      )}
    </VStack>
  )
}
