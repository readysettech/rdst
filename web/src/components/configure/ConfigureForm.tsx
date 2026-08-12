/**
 * Form component for configuring database targets
 */

import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { type ReactNode, useEffect, useState } from 'react'
import type {
  ConfigureConnectionStatus,
  ConfigureFormData,
} from '../../types/configure'
import { ConfigureConnectionTest } from './ConfigureConnectionTest'
import { FieldLabel } from './FieldLabel'
import { assembleSshConfig, SshFields, sshFieldsValue } from './SshFields'
import { WritePrivilegesNotice } from './WritePrivilegesNotice'

interface ConfigureFormProps {
  initialData?: Partial<ConfigureFormData>
  onSubmit?: (data: ConfigureFormData) => void
  onTest?: (
    data: ConfigureFormData
  ) =>
    | undefined
    | boolean
    | ConfigureConnectionStatus
    | Promise<boolean | ConfigureConnectionStatus | null>
  onCancel?: () => void
  isLoading?: boolean
  isTesting?: boolean
  testResult?: ConfigureConnectionStatus | null
  /** Override the add-mode submit label (e.g. "Test & connect" on first run). */
  submitLabel?: string
  /** Size of the primary submit button; first run uses a large hero CTA
   *  [VIS-022, VIS-035]. Defaults to `base` so other callers are unchanged. */
  submitSize?: 'base' | 'large'
  /** The unified Add connection drawer already owns the page title. */
  showHeader?: boolean
  /** Test unverified add-mode credentials before saving and confirm when the
   * connected role has write privileges. */
  reviewWritePrivilegesOnSubmit?: boolean
}

const engineOptions = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
]

/**
 * Progressive-disclosure section (built on the shared `use-disclosure` hook).
 * The panel stays mounted and toggles visibility via `hidden`, so collapsing a
 * section never unmounts its fields (form state is preserved, and pre-filled
 * values survive a collapse). [VIS-114, USE-088]
 */
function Disclosure({
  id,
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  id: string
  title: string
  subtitle?: string
  open: boolean
  onToggle: (open: boolean) => void
  children: ReactNode
}) {
  return (
    <div className="rounded-xl border border-border-layout-1 overflow-hidden">
      <Button
        type="button"
        label={title}
        icon={open ? 'chevron-up' : 'chevron-down'}
        iconPosition="right-full"
        modifier="ghost"
        fullWidth
        aria-expanded={open}
        aria-controls={id}
        onClick={() => onToggle(!open)}
        classMerge="h-auto min-h-12 rounded-none bg-transparent px-4 py-3 text-left text-content-layout-1 hover:bg-surface-layout-2/50"
      />
      <Show when={!!subtitle}>
        <Text
          level="caption"
          className="-mt-2 block px-4 pb-3 text-content-layout-3"
        >
          {subtitle}
        </Text>
      </Show>
      <div id={id} hidden={!open} className="px-4 pb-4 pt-1">
        {children}
      </div>
    </div>
  )
}

export function defaultPasswordEnv(targetName: string): string {
  const normalized = targetName
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return normalized ? `RDST_${normalized}_PASSWORD` : ''
}

interface ParsedConnectionUrl {
  engine: string
  host: string
  port: number
  database: string
  user: string
  password: string
  tls: boolean
  tlsVerify: boolean
  tlsCa: string
}

export const defaultPortFor = (engine: string) =>
  engine === 'mysql' ? 3306 : 5432

function parseConnectionUrl(url: string): ParsedConnectionUrl | null {
  try {
    // Handle postgres:// as alias for postgresql://
    const normalizedUrl = url.replace(/^postgres:\/\//, 'postgresql://')

    // Check for supported protocols
    if (
      !normalizedUrl.startsWith('postgresql://') &&
      !normalizedUrl.startsWith('mysql://')
    ) {
      return null
    }

    // Parse using URL API (replace protocol for parsing)
    const parsableUrl = normalizedUrl.replace(
      /^(postgresql|mysql):\/\//,
      'http://'
    )
    const parsed = new URL(parsableUrl)

    // Determine engine from original protocol
    const engine = normalizedUrl.startsWith('mysql://') ? 'mysql' : 'postgresql'
    const defaultPort = defaultPortFor(engine)

    // Extract database from pathname (remove leading slash)
    const database = parsed.pathname.replace(/^\//, '')
    const params = parsed.searchParams

    // Infer TLS/SSL from connection string query params
    let tls = false
    let tlsVerify = false
    let tlsCa = ''
    if (engine === 'postgresql') {
      const sslMode = (params.get('sslmode') || '').toLowerCase()
      tls =
        sslMode === 'require' ||
        sslMode === 'verify-ca' ||
        sslMode === 'verify-full'
      tlsVerify = sslMode === 'verify-ca' || sslMode === 'verify-full'
      tlsCa = params.get('sslrootcert') || ''
    } else {
      const ssl = (params.get('ssl') || '').toLowerCase()
      const sslMode = (params.get('ssl-mode') || '').toUpperCase()
      tls =
        ssl === 'true' ||
        ssl === '1' ||
        sslMode === 'REQUIRED' ||
        sslMode === 'VERIFY_CA' ||
        sslMode === 'VERIFY_IDENTITY'
      tlsVerify = sslMode === 'VERIFY_CA' || sslMode === 'VERIFY_IDENTITY'
      tlsCa = params.get('ssl-ca') || ''
    }

    return {
      engine,
      host: parsed.hostname || 'localhost',
      port: parsed.port ? Number.parseInt(parsed.port, 10) : defaultPort,
      database,
      user: parsed.username ? decodeURIComponent(parsed.username) : '',
      password: parsed.password ? decodeURIComponent(parsed.password) : '',
      tls,
      tlsVerify,
      tlsCa,
    }
  } catch {
    return null
  }
}

export function ConfigureForm({
  initialData,
  onSubmit,
  onTest,
  onCancel,
  isLoading,
  isTesting,
  testResult,
  submitLabel,
  submitSize = 'base',
  showHeader = true,
  reviewWritePrivilegesOnSubmit = false,
}: ConfigureFormProps) {
  const isAddMode = !initialData?.name
  const [connectionUrl, setConnectionUrl] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)
  const [name, setName] = useState(initialData?.name || '')
  const [engine, setEngine] = useState(initialData?.engine || 'postgresql')
  const defaultPort =
    initialData?.port ?? defaultPortFor(initialData?.engine || 'postgresql')
  const [host, setHost] = useState(initialData?.host || 'localhost')
  const [port, setPort] = useState(defaultPort)
  const [portTouched, setPortTouched] = useState(
    initialData?.port !== undefined &&
      initialData.port !== defaultPortFor(initialData.engine || 'postgresql')
  )
  const [database, setDatabase] = useState(initialData?.database || '')
  const [user, setUser] = useState(initialData?.user || '')
  const [password, setPassword] = useState('')
  const [tls, setTls] = useState(initialData?.tls ?? false)
  const [tlsVerify, setTlsVerify] = useState(initialData?.tls_verify ?? false)
  const [tlsCa, setTlsCa] = useState(initialData?.tls_ca ?? '')
  const readOnly = initialData?.read_only ?? false
  const [ssh, setSsh] = useState(() => sshFieldsValue(initialData?.ssh))
  const [pendingWritableSubmission, setPendingWritableSubmission] = useState<{
    data: ConfigureFormData
    result: ConfigureConnectionStatus
  } | null>(null)
  const [explicitConnectionTest, setExplicitConnectionTest] = useState<{
    fingerprint: string
    connected: boolean
    result?: ConfigureConnectionStatus
  } | null>(null)

  useEffect(() => {
    setPendingWritableSubmission(null)
  }, [
    name,
    engine,
    host,
    port,
    database,
    user,
    password,
    tls,
    tlsVerify,
    tlsCa,
    ssh,
  ])

  // "Connection details" holds the fields the connection needs, so it opens by
  // default; "Advanced" TLS settings stay collapsed until asked for. A
  // paste reveals both so the auto-filled values — including the inferred TLS —
  // are visible for review. [VIS-114, USE-067]
  const [detailsOpenState, setDetailsOpenState] = useState(true)
  const [detailsOpen, setDetailsOpen] = useDisclosure({
    open: detailsOpenState,
    onOpenChange: setDetailsOpenState,
  })
  const [advancedOpen, setAdvancedOpen] = useDisclosure({})
  const [sshOpenState, setSshOpenState] = useState(Boolean(initialData?.ssh))
  const [sshOpen, setSshOpen] = useDisclosure({
    open: sshOpenState,
    onOpenChange: setSshOpenState,
  })

  const handleParseUrl = () => {
    setUrlError(null)
    const trimmedUrl = connectionUrl.trim()

    if (!trimmedUrl) {
      setUrlError('Please enter a connection URL')
      return
    }

    const parsed = parseConnectionUrl(trimmedUrl)
    if (!parsed) {
      setUrlError(
        'Invalid URL format. Expected: postgresql://user@host:port/database'
      )
      return
    }

    setEngine(parsed.engine)
    setHost(parsed.host)
    setPort(parsed.port)
    setPortTouched(parsed.port !== defaultPortFor(parsed.engine))
    setDatabase(parsed.database)
    setUser(parsed.user)
    setPassword(parsed.password)
    setTls(parsed.tls)
    setTlsVerify(parsed.tlsVerify)
    setTlsCa(parsed.tlsCa)

    // Auto-generate name from database if not already set
    if (!name && parsed.database) {
      setName(parsed.database)
    }

    // Reveal the pre-filled fields (and the inferred TLS in Advanced) for review.
    setDetailsOpen(true)
    setAdvancedOpen(true)

    // Clear the URL field after successful parse
    setConnectionUrl('')
  }

  const isAddModePasswordValid = !isAddMode || password.length > 0
  const isValid =
    name && host && port && database && user && isAddModePasswordValid

  const currentData = (): ConfigureFormData => ({
    name,
    engine,
    host,
    port,
    database,
    user,
    password: password || undefined,
    password_env: initialData?.password_env,
    tls,
    tls_verify: tlsVerify,
    tls_ca: tlsCa.trim() || undefined,
    read_only: readOnly,
    ssh: assembleSshConfig(ssh),
  })

  const fingerprint = (data: ConfigureFormData) => JSON.stringify(data)

  const handleExplicitTest = async () => {
    if (!onTest) return
    const data = currentData()
    const result = await onTest(data)
    setExplicitConnectionTest({
      fingerprint: fingerprint(data),
      connected:
        typeof result === 'boolean' ? result : Boolean(result?.connected),
      result: typeof result === 'object' && result ? result : undefined,
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!isValid || !onSubmit) return

    const data = currentData()
    const explicitlyTested =
      explicitConnectionTest?.fingerprint === fingerprint(data) &&
      explicitConnectionTest.connected
    if (isAddMode && reviewWritePrivilegesOnSubmit && onTest) {
      if (explicitlyTested) {
        const result = explicitConnectionTest.result
        if (result?.privileges?.writable === true) {
          setPendingWritableSubmission({ data, result })
          return
        }
      } else {
        const result = await onTest(data)
        const connected =
          typeof result === 'boolean' ? result : Boolean(result?.connected)

        if (!connected) return
        if (
          typeof result === 'object' &&
          result?.privileges?.writable === true
        ) {
          setPendingWritableSubmission({ data, result })
          return
        }
      }
    }

    onSubmit(data)
  }

  const handleCancel = () => {
    const resetEngine = initialData?.engine || 'postgresql'
    setName('')
    setEngine(resetEngine)
    setHost('localhost')
    setPort(initialData?.port ?? defaultPortFor(resetEngine))
    setPortTouched(false)
    setDatabase('')
    setUser('')
    setPassword('')
    setTls(false)
    setTlsVerify(false)
    setTlsCa('')
    setSsh(sshFieldsValue())
    onCancel?.()
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <Card className="w-full">
          <Show when={showHeader}>
            <Card.Header>
              <HStack className="gap-2 items-center">
                <Icon
                  name={isAddMode ? 'add' : 'edit'}
                  label={isAddMode ? 'Add' : 'Edit'}
                  className="w-4 h-4 text-content-layout-3"
                />
                <Text level="label-medium" className="text-content-layout-1">
                  {initialData?.name ? 'Edit connection' : 'New connection'}
                </Text>
              </HStack>
            </Card.Header>
          </Show>
          <Card.Content>
            <div className="space-y-5">
              {/* Quick Setup — the primary path. Hidden when editing a known
                connection (there's no string to paste). The parser itself stays
                intact for add mode. [USE-067, VIS-121] */}
              {isAddMode && (
                <div className="rounded-xl bg-surface-layout-2/50 p-4">
                  <HStack className="gap-2 items-center mb-3">
                    <Icon
                      name="connect"
                      label="Quick setup"
                      className="w-4 h-4 text-content-primary-soft"
                    />
                    <label htmlFor="cfg-connection-url">
                      <Text
                        as="span"
                        level="label-small"
                        className="text-content-primary-soft"
                      >
                        Quick Setup
                      </Text>
                    </label>
                  </HStack>
                  <div className="space-y-2">
                    <div className="flex gap-2">
                      <div className="flex-1">
                        <BaseInputText
                          id="cfg-connection-url"
                          name="connectionUrl"
                          value={connectionUrl}
                          onChange={(e) => {
                            setConnectionUrl(e.target.value)
                            setUrlError(null)
                          }}
                          placeholder={
                            engine === 'mysql'
                              ? 'mysql://user@host:3306/database'
                              : 'postgresql://user@host:5432/database'
                          }
                          disabled={isLoading}
                        />
                      </div>
                      <Button
                        variant="primary"
                        modifier="outline"
                        label="Parse"
                        type="button"
                        onClick={handleParseUrl}
                        disabled={isLoading || !connectionUrl.trim()}
                      />
                    </div>
                    <Show when={!!urlError}>
                      <Text
                        level="body-small"
                        className="text-content-negative-soft"
                      >
                        {urlError}
                      </Text>
                    </Show>
                    <Text level="caption" className="text-content-layout-3">
                      Paste a connection URL to auto-fill the form fields
                    </Text>
                  </div>
                </div>
              )}

              {/* Name — always visible; it's the identity you'll pick the
                connection by. */}
              <div>
                <FieldLabel htmlFor="cfg-name">Target Name *</FieldLabel>
                <BaseInputText
                  id="cfg-name"
                  name="name"
                  value={name}
                  onChange={(e) => {
                    const nextName = e.target.value
                    setName(nextName)
                  }}
                  placeholder="my-database"
                  disabled={isLoading || !!initialData?.name}
                  required
                />
                <Show when={!isAddMode}>
                  <Text level="caption" className="text-content-layout-3 mt-1">
                    The name can't be changed after a connection is created.
                  </Text>
                </Show>
              </div>

              {/* Connection details — the manual field grid, deferred behind a
                disclosure that opens pre-filled after a paste. [USE-067, VIS-114] */}
              <Disclosure
                id="cfg-connection-details"
                title="Connection details"
                subtitle="Engine, host, port, database, user, password"
                open={detailsOpen}
                onToggle={setDetailsOpen}
              >
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <FieldLabel htmlFor="cfg-engine">
                        Database Engine *
                      </FieldLabel>
                      <BaseInputSelect
                        id="cfg-engine"
                        name="engine"
                        options={engineOptions}
                        value={engine}
                        onValueChange={(nextEngine) => {
                          setEngine(nextEngine)
                          if (!portTouched) setPort(defaultPortFor(nextEngine))
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div>
                      <FieldLabel htmlFor="cfg-host">Host *</FieldLabel>
                      <BaseInputText
                        id="cfg-host"
                        name="host"
                        value={host}
                        onChange={(e) => setHost(e.target.value)}
                        placeholder={
                          engine === 'mysql'
                            ? 'mysql.example.com'
                            : 'postgres.example.com'
                        }
                        disabled={isLoading}
                        required
                      />
                    </div>

                    <div>
                      <FieldLabel htmlFor="cfg-port">Port *</FieldLabel>
                      <BaseInputText
                        id="cfg-port"
                        name="port"
                        type="number"
                        value={String(port)}
                        onChange={(e) => {
                          setPortTouched(true)
                          setPort(
                            Number(e.target.value) || defaultPortFor(engine)
                          )
                        }}
                        placeholder={String(defaultPortFor(engine))}
                        disabled={isLoading}
                        required
                      />
                    </div>

                    <div>
                      <FieldLabel htmlFor="cfg-database">Database *</FieldLabel>
                      <BaseInputText
                        id="cfg-database"
                        name="database"
                        value={database}
                        onChange={(e) => setDatabase(e.target.value)}
                        placeholder="myapp"
                        disabled={isLoading}
                        required
                      />
                    </div>

                    <div>
                      <FieldLabel htmlFor="cfg-user">User *</FieldLabel>
                      <BaseInputText
                        id="cfg-user"
                        name="user"
                        value={user}
                        onChange={(e) => setUser(e.target.value)}
                        placeholder={engine === 'mysql' ? 'root' : 'postgres'}
                        disabled={isLoading}
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <FieldLabel htmlFor="cfg-password">
                      Database Password {isAddMode ? '*' : ''}
                    </FieldLabel>
                    <BaseInputText
                      id="cfg-password"
                      name="password"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder={
                        isAddMode
                          ? 'Enter database password'
                          : 'Leave blank to keep current password'
                      }
                      disabled={isLoading}
                      required={isAddMode}
                      autoComplete="new-password"
                    />
                    <Text
                      level="caption"
                      className="text-content-layout-3 mt-1"
                    >
                      Stored in your local secret store, never in the target
                      configuration
                    </Text>
                  </div>
                </div>
              </Disclosure>

              <Disclosure
                id="cfg-ssh"
                title="Connect via SSH jump host"
                subtitle="For databases that are not directly reachable"
                open={sshOpen}
                onToggle={setSshOpen}
              >
                <SshFields
                  value={ssh}
                  onChange={setSsh}
                  disabled={isLoading}
                  idPrefix="cfg-ssh"
                />
              </Disclosure>

              {/* Advanced — TLS verification, collapsed until asked for. [VIS-114] */}
              <Disclosure
                id="cfg-advanced"
                title="Advanced"
                subtitle="TLS and certificate verification"
                open={advancedOpen}
                onToggle={setAdvancedOpen}
              >
                <div className="space-y-4">
                  <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                    <label htmlFor="cfg-tls" className="cursor-pointer">
                      <VStack className="gap-0.5 items-start">
                        <Text
                          as="span"
                          level="label-small"
                          className="text-content-layout-1"
                        >
                          TLS encryption
                        </Text>
                        <Text
                          as="span"
                          level="caption"
                          className="text-content-layout-3"
                        >
                          Require encrypted connection
                        </Text>
                      </VStack>
                    </label>
                    <BaseInputSwitch
                      id="cfg-tls"
                      name="tls"
                      aria-label="TLS encryption"
                      checked={tls}
                      onCheckedChange={(next) => {
                        setTls(next)
                        if (!next) setTlsVerify(false)
                      }}
                      disabled={isLoading}
                    />
                  </div>

                  <Show when={tls}>
                    <div className="space-y-4">
                      <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                        <label
                          htmlFor="cfg-tls-verify"
                          className="cursor-pointer"
                        >
                          <VStack className="gap-0.5 items-start">
                            <Text
                              as="span"
                              level="label-small"
                              className="text-content-layout-1"
                            >
                              Verify TLS certificate
                            </Text>
                            <Text
                              as="span"
                              level="caption"
                              className="text-content-layout-3"
                            >
                              Verify the certificate chain and database hostname
                            </Text>
                          </VStack>
                        </label>
                        <BaseInputSwitch
                          id="cfg-tls-verify"
                          name="tls_verify"
                          aria-label="Verify TLS certificate"
                          checked={tlsVerify}
                          onCheckedChange={setTlsVerify}
                          disabled={isLoading}
                        />
                      </div>
                      <Show when={tlsVerify}>
                        <div>
                          <FieldLabel htmlFor="cfg-tls-ca">
                            TLS CA path
                          </FieldLabel>
                          <BaseInputText
                            id="cfg-tls-ca"
                            name="tls_ca"
                            value={tlsCa}
                            onChange={(event) => setTlsCa(event.target.value)}
                            placeholder="/path/to/ca-certificate.pem (optional)"
                            disabled={isLoading}
                          />
                        </div>
                      </Show>
                    </div>
                  </Show>
                </div>
              </Disclosure>
            </div>
          </Card.Content>
          <Show when={isTesting || !!testResult}>
            <div className="px-5 pb-2">
              <ConfigureConnectionTest
                result={testResult ?? null}
                isLoading={isTesting}
                targetName={name.trim() || 'form-test'}
                database={database.trim()}
                onRetry={async () => {
                  const result = await onTest?.(currentData())
                  return typeof result === 'boolean'
                    ? result
                    : Boolean(result?.connected)
                }}
              />
            </div>
          </Show>
          <Card.Footer>
            <HStack className="gap-3 justify-end w-full">
              <Button
                variant="primary"
                modifier="ghost"
                label="Cancel"
                onClick={handleCancel}
                type="button"
              />
              <Button
                variant="primary"
                modifier="outline"
                label="Test connection"
                icon="connect"
                iconPosition="left"
                onClick={() => void handleExplicitTest()}
                loading={isTesting}
                disabled={!isValid || isLoading || isTesting || !onTest}
                type="button"
              />
              <Button
                variant="rising"
                modifier="solid"
                size={submitSize}
                label={
                  initialData?.name
                    ? 'Update connection'
                    : (submitLabel ?? 'Add connection')
                }
                type="submit"
                loading={isLoading}
                disabled={!isValid || isTesting}
              />
            </HStack>
          </Card.Footer>
        </Card>
      </form>
      <ConfirmDialog
        isOpen={pendingWritableSubmission !== null}
        onClose={() => setPendingWritableSubmission(null)}
        onConfirm={() => {
          if (!pendingWritableSubmission) return
          onSubmit?.(pendingWritableSubmission.data)
          setPendingWritableSubmission(null)
        }}
        title="Use this database account?"
        subtitle={
          pendingWritableSubmission
            ? `${pendingWritableSubmission.data.name} connected successfully. RDST recommends read-only access, but you can add this account as-is.`
            : undefined
        }
        confirmLabel="Add connection"
        confirmVariant="primary"
        size="large"
      >
        {pendingWritableSubmission ? (
          <WritePrivilegesNotice
            engine={
              pendingWritableSubmission.result.databaseEngine ??
              pendingWritableSubmission.data.engine
            }
            database={pendingWritableSubmission.data.database}
          />
        ) : null}
      </ConfirmDialog>
    </>
  )
}
