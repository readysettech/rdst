// Add or edit a database in one dialog. Adding: the providers to import from, one quiet row
// each, then one database by hand (a connection string or the fields under it), an optional
// name and Advanced, a ghost "Test" and one solid "Add and monitor", which tests, saves and opens
// the database's page on the turn-on dialog (the prerequisites check and the EXPLAIN ANALYZE
// warning). A test that finds the query statistics unreadable shows what to change, and the
// database isn't added for monitoring until they're readable; a quiet link adds it with
// monitoring off. Editing: the saved fields, the name and Advanced, with "Test" and "Save
// changes".
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import { CodeBlock, Steps } from '../../features/prerequisites/FixPanel'
import { useTarget } from '../../hooks/useTarget'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { invalidateTargetQueries } from '../../lib/targetQueries'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import type {
  ConfigureConnectionStatus,
  ConfigureFormData,
  ConfigureTargetDetail,
} from '../../types/configure'
import {
  type AddRequest,
  notifyDatabasesChanged,
  useAddDatabaseRequest,
} from './addDatabase'
import type { AddTab } from './addTabs'
import {
  type Advanced,
  AdvancedFields,
  advancedConnection,
  advancedFrom,
  nameError,
} from './ConfigureForm'
import { defaultPortFor, parseConnectionUrl } from './connectionUrl'
import { Field } from './Field'
import {
  type ImportStep,
  ProviderImport,
  SourceList,
  sourceLabel,
} from './ProviderImport'

interface Fields {
  host: string
  port: string
  database: string
  user: string
  password: string
}

const EMPTY: Fields = {
  host: '',
  port: '',
  database: '',
  user: '',
  password: '',
}

const TITLES: Record<Exclude<ImportStep, 'source'>, string> = {
  credentials: 'Set credentials',
  prereqs: 'Set up monitoring',
}

const EXPECTED_URL = 'Expected postgresql://user@host:5432/database'
const MISSING_FIELDS =
  'Fill in the host, database and user, or paste a connection string.'

/** A name for the new database: its database name, made unique among the saved ones. */
export function newDatabaseName(
  base: string,
  taken: readonly string[]
): string {
  const root =
    base
      .trim()
      .replace(/[^A-Za-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'database'
  if (!taken.includes(root)) return root
  for (let n = 2; n <= taken.length + 1; n += 1) {
    if (!taken.includes(`${root}-${n}`)) return `${root}-${n}`
  }
  return `${root}-${taken.length + 2}`
}

/**
 * The form's connection: the string when one is typed (it fills the fields), the
 * fields otherwise, with `engine` when one was picked. Null while required parts are
 * missing or the string can't be read.
 */
export function connectionFrom(
  url: string,
  fields: Fields,
  engine?: string | null
): Omit<ConfigureFormData, 'name'> | null {
  if (url.trim()) {
    const parsed = parseConnectionUrl(url.trim())
    if (!parsed || !parsed.database || !parsed.user) return null
    return {
      engine: parsed.engine,
      host: parsed.host,
      port: parsed.port,
      database: parsed.database,
      user: parsed.user,
      password: parsed.password || undefined,
      tls: parsed.tls,
      tls_verify: parsed.tlsVerify,
      tls_ca: parsed.tlsCa || undefined,
    }
  }
  // An empty port reads as the engine's default, PostgreSQL's when none was picked.
  const port = fields.port.trim()
    ? Number(fields.port)
    : defaultPortFor(engine ?? 'postgresql')
  if (!fields.host.trim() || !Number.isInteger(port) || port <= 0) return null
  if (!fields.database.trim() || !fields.user.trim()) return null
  return {
    engine:
      engine ?? (port === defaultPortFor('mysql') ? 'mysql' : 'postgresql'),
    host: fields.host.trim(),
    port,
    database: fields.database.trim(),
    user: fields.user.trim(),
    password: fields.password || undefined,
  }
}

/** "PostgreSQL 16.3" out of the server's version string. */
export function serverVersion(raw?: string | null): string | null {
  return raw?.match(/^(PostgreSQL|MySQL)\s+[^\s,]+/)?.[0] ?? null
}

type QueryCapture = NonNullable<ConfigureConnectionStatus['queryCapture']>

type Check =
  | {
      connected: true
      version: string | null
      /** Set when the server's query statistics can't be read yet. */
      capture: QueryCapture | null
      /** The server said its query statistics are readable. */
      statsReadable?: boolean
    }
  | { connected: false; message: string }

function Divider({ children }: { children: string }) {
  return (
    <div className="mt-4 mb-3.5 flex items-center gap-2.5 text-caption text-content-layout-3">
      <span aria-hidden="true" className="h-px flex-1 bg-border-layout-2" />
      {children}
      <span aria-hidden="true" className="h-px flex-1 bg-border-layout-2" />
    </div>
  )
}

/** What to change on the server so monitoring can read its query statistics. */
function CaptureNotice({
  capture,
  onAddWithoutMonitoring,
  disabled,
}: {
  capture: QueryCapture
  /** Saves the database with monitoring off; set while adding, not editing. */
  onAddWithoutMonitoring?: () => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-3">
      <Alert
        variant="warning"
        modifier="ghost"
        label="Monitoring can't read query statistics yet"
      />
      <Steps steps={capture.instructions} start={1} />
      {(capture.config ?? []).map((line) => (
        <CodeBlock key={line} code={line} kind="shell" />
      ))}
      {(capture.sql ?? []).map((statement) => (
        <CodeBlock key={statement} code={statement} kind="sql" />
      ))}
      <Text as="p" level="caption" className="text-content-layout-3">
        Then click Add and monitor again.
      </Text>
      {onAddWithoutMonitoring && (
        <p className="pt-2 text-caption text-content-layout-3">
          <button
            type="button"
            className="underline decoration-border-layout-2 underline-offset-2 hover:text-content-layout-2 disabled:opacity-50"
            disabled={disabled}
            onClick={onAddWithoutMonitoring}
          >
            Add without monitoring
          </button>{' '}
          RDST can't watch this database until the setup steps above are done.
        </p>
      )}
    </div>
  )
}

const fieldsOf = (saved?: ConfigureTargetDetail): Fields =>
  saved
    ? {
        host: saved.host,
        port: String(saved.port),
        database: saved.database,
        user: saved.user,
        password: '',
      }
    : EMPTY

function ConnectionForm({
  onImport,
  editing,
  onSaved,
}: {
  onImport: (tab: AddTab) => void
  /** The saved database being edited; adding when absent. */
  editing?: ConfigureTargetDetail
  onSaved?: (name: string) => void
}) {
  const configure = useConfigure()
  const status = useSystemStatus()
  const { target: selected } = useTarget()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [fields, setFields] = useState<Fields>(() => fieldsOf(editing))
  const [name, setName] = useState(editing?.name ?? '')
  const [advanced, setAdvanced] = useState<Advanced>(() =>
    advancedFrom(editing)
  )
  const [check, setCheck] = useState<Check | null>(null)
  const [phase, setPhase] = useState<'testing' | 'saving' | null>(null)
  const [attempted, setAttempted] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)

  const taken = (status.data?.targets ?? [])
    .map((target) => target.name)
    .filter((other) => other !== editing?.name)
  const base = connectionFrom(url, fields, advanced.engine)
  const connection = base && {
    ...base,
    ...(advanced.engine ? { engine: advanced.engine } : {}),
    ...advancedConnection(advanced),
    read_only: editing?.read_only,
  }
  const urlUnreadable = Boolean(url.trim()) && !connection
  const defaultName = editing
    ? editing.name
    : newDatabaseName(fields.database || fields.host, taken)
  const problem =
    (attempted &&
      (urlUnreadable
        ? EXPECTED_URL
        : !connection
          ? editing
            ? 'Fill in the host, database and user.'
            : MISSING_FIELDS
          : nameError(name, taken))) ||
    (check && !check.connected ? check.message : null)
  const capture = check?.connected ? check.capture : null

  useEffect(() => {
    if (problem || check?.connected)
      errorRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [problem, check?.connected])

  const changeUrl = (value: string) => {
    setUrl(value)
    setCheck(null)
    const parsed = parseConnectionUrl(value.trim())
    if (parsed) {
      setFields({
        host: parsed.host,
        port: String(parsed.port),
        database: parsed.database,
        user: parsed.user,
        password: parsed.password,
      })
      setAdvanced((current) => ({
        ...current,
        engine: null,
        tls: parsed.tls,
        tlsVerify: parsed.tlsVerify,
        tlsCa: parsed.tlsCa,
      }))
    }
  }
  const set = (field: keyof Fields) => (value: string) => {
    setFields((current) => ({ ...current, [field]: value }))
    setCheck(null)
  }
  const changeAdvanced = (next: Advanced) => {
    setAdvanced(next)
    setCheck(null)
  }

  /** The form to send, or null when it can't go yet (the reason shows at the top). */
  const ready = (): ConfigureFormData | null => {
    setAttempted(true)
    if (!connection || nameError(name, taken)) return null
    return { ...connection, name: name.trim() || defaultName }
  }

  // An edit tests under the saved name, so the saved password fills a blank one.
  const runTest = async (data: ConfigureFormData): Promise<Check> => {
    const result = await configure.testConnection(
      editing?.name ?? data.name,
      data
    )
    const next: Check = result?.connected
      ? {
          connected: true,
          version: serverVersion(result.engine),
          capture:
            result.queryCapture?.available === false
              ? result.queryCapture
              : null,
          statsReadable: result.queryCapture?.available === true,
        }
      : {
          connected: false,
          message: result?.error || "RDST couldn't connect to this database.",
        }
    setCheck(next)
    return next
  }

  const fail = (caught: unknown, fallback: string) => {
    setPhase(null)
    setCheck({
      connected: false,
      message: caught instanceof Error ? caught.message : fallback,
    })
  }

  const test = async () => {
    const data = ready()
    if (!data) return
    setPhase('testing')
    try {
      await runTest(data)
    } finally {
      setPhase(null)
    }
  }

  const save = async () => {
    const data = ready()
    if (!data || !editing) return
    setPhase('saving')
    try {
      await configure.updateTarget(editing.name, data)
    } catch (caught) {
      fail(caught, "The changes weren't saved.")
      return
    }
    if (data.name !== editing.name && selected === editing.name)
      selectGlobalTarget(data.name)
    await invalidateTargetQueries(queryClient)
    notifyDatabasesChanged({ recheck: [data.name] })
    setPhase(null)
    onSaved?.(data.name)
  }

  // `monitor` false saves a database whose query statistics aren't readable yet, with
  // monitoring off, after the test that found them unreadable.
  const add = async (monitor = true) => {
    const data = ready()
    if (!data) return
    setPhase('saving')
    try {
      if (monitor) {
        const checked = await runTest(data)
        // Unreadable query statistics hold the add until they're fixed.
        if (!checked.connected || checked.capture) {
          setPhase(null)
          return
        }
      }
      await configure.addTarget(data)
      if (taken.length === 0) await configure.setDefaultTarget(data.name)
    } catch (caught) {
      fail(caught, "The database wasn't added.")
      return
    }
    startBootstrapRun(data.name)
    // Monitoring turns on from the turn-on dialog, after its checks and the EXPLAIN ANALYZE
    // warning.
    selectGlobalTarget(data.name)
    await queryClient.refetchQueries({ queryKey: ['status'] })
    notifyDatabasesChanged()
    void navigate({
      to: '/database',
      search: monitor
        ? { target: data.name, panel: 'enable' }
        : { target: data.name },
    })
  }

  const busy = phase !== null
  const fromUrl = Boolean(url.trim())
  const nameField = (
    <Field
      label="Name"
      name="name"
      value={name}
      onChange={setName}
      placeholder={defaultName}
      disabled={busy}
      className="tablet:col-span-2"
    />
  )
  return (
    <>
      <Scrollable rootClassName="min-h-0" className="px-6 pt-2 pb-5">
        {(problem || check?.connected) && (
          <div ref={errorRef} className="mt-2 mb-3 space-y-3">
            {problem && (
              <Alert variant="negative" modifier="ghost" label={problem} />
            )}
            {check?.connected && !problem && (
              <Text
                as="p"
                level="caption"
                className="text-content-positive-soft"
              >
                {check.version
                  ? `Connected to ${check.version}.`
                  : 'Connected.'}
                {check.statsReadable && ' Query statistics are readable.'}
              </Text>
            )}
            {capture && (
              <CaptureNotice
                capture={capture}
                disabled={busy}
                onAddWithoutMonitoring={
                  editing ? undefined : () => void add(false)
                }
              />
            )}
          </div>
        )}
        {!editing && (
          <>
            <Text
              as="p"
              level="caption"
              className="mt-2 mb-1.5 text-content-layout-3"
            >
              Import from
            </Text>
            <SourceList onPick={onImport} />
            <Divider>or connect one by hand</Divider>
            <Field
              label="Connection string"
              name="connection_string"
              value={url}
              onChange={changeUrl}
              placeholder="postgresql://user@host:5432/database"
              disabled={busy}
              mono
            />
            <Divider>or fill in the fields</Divider>
          </>
        )}
        <div className="mt-2 grid grid-cols-1 gap-3 tablet:grid-cols-2">
          {editing && nameField}
          <Field
            label="Host"
            name="host"
            value={fields.host}
            onChange={set('host')}
            placeholder="localhost"
            disabled={busy || fromUrl}
          />
          <Field
            label="Port"
            name="port"
            value={fields.port}
            onChange={set('port')}
            placeholder={String(
              defaultPortFor(connection?.engine ?? 'postgresql')
            )}
            disabled={busy || fromUrl}
          />
          <Field
            label="Database"
            name="database"
            value={fields.database}
            onChange={set('database')}
            disabled={busy || fromUrl}
          />
          <Field
            label="User"
            name="user"
            value={fields.user}
            onChange={set('user')}
            disabled={busy || fromUrl}
          />
          <Field
            label="Password"
            name="password"
            type="password"
            value={fields.password}
            onChange={set('password')}
            placeholder={
              editing?.has_password ? 'Leave blank to keep it' : undefined
            }
            helper={
              editing
                ? 'Stored in your local secret store, never in the database configuration.'
                : undefined
            }
            disabled={busy || fromUrl}
            className="tablet:col-span-2"
          />
          {!editing && nameField}
        </div>
        <AdvancedFields
          value={advanced}
          engine={connection?.engine ?? advanced.engine ?? 'postgresql'}
          onChange={changeAdvanced}
          disabled={busy}
          defaultOpen={Boolean(editing?.ssh || editing?.tls)}
        />
      </Scrollable>
      <div className="flex items-center gap-2 border-t border-border-layout-2 px-6 py-3.5">
        <Text
          as="p"
          level="caption"
          className="mr-1 min-w-0 flex-1 text-content-layout-3"
        >
          {editing
            ? 'Monitoring only reads.'
            : 'RDST checks what monitoring needs and asks before it turns on.'}
        </Text>
        <Button
          variant="primary"
          modifier="ghost"
          label="Test"
          aria-label="Test"
          loading={phase === 'testing'}
          disabled={busy}
          onClick={() => void test()}
        />
        <Button
          variant="primary"
          modifier="solid"
          label={
            editing
              ? check && !check.connected
                ? 'Save anyway'
                : 'Save changes'
              : 'Add and monitor'
          }
          loading={phase === 'saving'}
          disabled={busy}
          onClick={() => void (editing ? save() : add())}
        />
      </div>
    </>
  )
}

type Importing = AddTab | null

const importingFor = (request: AddRequest): Importing =>
  request === 'form' ? null : request

export function AddDatabaseDialog({
  request,
  edit,
  onClose,
  onSaved,
}: {
  /** What to open on; null keeps the dialog closed unless `edit` is set. */
  request: AddRequest | null
  /** A saved database to edit: the dialog opens on its fields. */
  edit?: ConfigureTargetDetail | null
  onClose: () => void
  /** An edit was saved, under this name. */
  onSaved?: (name: string) => void
}) {
  const open = request !== null || Boolean(edit)
  // Every opening starts on its request's view; closing keeps the view for the animation.
  const [seen, setSeen] = useState<AddRequest | null>(request)
  const [opening, setOpening] = useState(0)
  const [importing, setImporting] = useState<Importing>(
    importingFor(request ?? 'form')
  )
  const [step, setStep] = useState<ImportStep>('source')
  const [lastEdit, setLastEdit] = useState(edit)
  if (request !== seen) {
    setSeen(request)
    if (request) {
      setOpening((count) => count + 1)
      setImporting(importingFor(request))
      setStep('source')
    }
  }
  if (edit && edit !== lastEdit) setLastEdit(edit)
  const editing = edit ?? (open ? null : lastEdit)
  const title = editing
    ? 'Edit database'
    : importing
      ? step === 'source'
        ? `Import from ${sourceLabel(importing)}`
        : TITLES[step]
      : 'Add a database'
  // The title sits in this tree so the dialog takes its accessible name from it.
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent
          size="base"
          onEscapeKeyDown={(event) => {
            // Escape in a provider view goes back to the form, as Back does.
            if (editing || !importing || step !== 'source') return
            event.preventDefault()
            setImporting(null)
          }}
          className="max-w-120 border-border-layout-2 bg-surface-primary-soft p-0"
          closeClassName="top-4.5 right-4"
          description={
            editing
              ? `How RDST connects to ${editing.name}.`
              : 'Connect a database for RDST to monitor.'
          }
          data-testid="add-database"
        >
          <div className="flex items-center gap-2 pt-4.5 pr-14 pl-6">
            {!editing && importing && step === 'source' && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                icon="arrow-left"
                label="Back"
                onClick={() => setImporting(null)}
              />
            )}
            <ModalTitle className="h-auto text-headline-5">{title}</ModalTitle>
          </div>
          {editing ? (
            <ConnectionForm
              key={editing.name}
              onImport={setImporting}
              editing={editing}
              onSaved={onSaved}
            />
          ) : importing ? (
            <ProviderImport
              key={opening}
              initialTab={importing}
              onClose={onClose}
              onStepChange={setStep}
            />
          ) : (
            <ConnectionForm onImport={setImporting} />
          )}
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}

/** The dialog wired to `?add=`: it opens over whichever page asked for it. */
export function AddDatabaseHost() {
  const { request, close } = useAddDatabaseRequest()
  return <AddDatabaseDialog request={request} onClose={close} />
}
