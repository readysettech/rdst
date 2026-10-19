// Add or edit a database in one dialog. Adding: the coding agent path, the providers to import
// from, one quiet row each, then one database by hand (a connection string or the fields under
// it), an optional name and Advanced, a ghost "Test" and one solid "Add and monitor", which
// tests, saves and opens the database's page on the turn-on dialog (the prerequisites check
// and the EXPLAIN ANALYZE warning). Query statistics that aren't on yet are fixed there, the
// same place the agent path lands. Editing: the saved fields, the name and Advanced, with
// "Test" and "Save changes".
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
import { StatsBlocked } from '../../features/prerequisites/StatsBlocked'
import { useTarget } from '../../hooks/useTarget'
import {
  type AddAttempt,
  abandonAttempt,
  handOffAddFlow,
  newAddAttempt,
  onceForAttempt,
} from '../../lib/addFlow'
import {
  type AddDatabaseEntry,
  type DatabaseAddField,
  type DatabaseAddFieldError,
  type DatabaseAddStage,
  type DatabaseAddVia,
  trackEvent,
  trackEventOnce,
} from '../../lib/analytics'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { invalidateTargetQueries } from '../../lib/targetQueries'
import { useConfigure } from '../../lib/useConfigure'
import { userErrorClass } from '../../lib/userErrorClass'
import { useSystemStatus } from '../../lib/useSystemStatus'
import type {
  ConfigureConnectionStatus,
  ConfigureFormData,
  ConfigureTargetDetail,
} from '../../types/configure'
import { GetStartedStrip } from '../GetStarted'
import { AgentConnect, AgentConnectOption, DataLine } from './AgentConnect'
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
  editing,
}: {
  capture: QueryCapture
  editing: boolean
}) {
  return (
    <div className="space-y-3" data-testid="capture-notice">
      <StatsBlocked source={capture.source} />
      <Steps steps={capture.instructions} start={1} />
      {(capture.config ?? []).map((line) => (
        <CodeBlock key={line} code={line} kind="shell" />
      ))}
      {(capture.sql ?? []).map((statement) => (
        <CodeBlock key={statement} code={statement} kind="sql" />
      ))}
      <Text as="p" level="caption" className="text-content-layout-3">
        {editing
          ? 'Turn them on, then test again.'
          : "You can add it now. RDST checks again on the next step and won't monitor until they're on."}
      </Text>
    </div>
  )
}

/** Which fields a submit can't use, by name only. */
export function fieldErrors(
  url: string,
  fields: Fields,
  nameProblem: boolean
): [DatabaseAddField, DatabaseAddFieldError][] {
  const errors: [DatabaseAddField, DatabaseAddFieldError][] = []
  if (url.trim()) {
    const parsed = parseConnectionUrl(url.trim())
    if (!parsed || !parsed.database || !parsed.user)
      errors.push(['connection_string', 'unreadable'])
  } else {
    if (!fields.host.trim()) errors.push(['host', 'missing'])
    const port = fields.port.trim()
    if (port && !(Number.isInteger(Number(port)) && Number(port) > 0))
      errors.push(['port', 'invalid'])
    if (!fields.database.trim()) errors.push(['database', 'missing'])
    if (!fields.user.trim()) errors.push(['user', 'missing'])
  }
  if (nameProblem) errors.push(['name', 'invalid'])
  return errors
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
  onAgent,
  editing,
  onSaved,
  attempt,
}: {
  onImport: (tab: AddTab) => void
  /** Opens the coding agent path; set while adding. */
  onAgent?: () => void
  /** The saved database being edited; adding when absent. */
  editing?: ConfigureTargetDetail
  onSaved?: (name: string) => void
  /** The dialog's add attempt, for the funnel it reports; set while adding. */
  attempt?: AddAttempt
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
  // Set when the OS keyring kept no copy of the password: the warning shows, and
  // Continue carries on to the database.
  const [sessionOnly, setSessionOnly] = useState<(() => void) | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)
  const pathsChosen = useRef(new Set<DatabaseAddVia>())
  const via: DatabaseAddVia = url.trim() ? 'url' : 'fields'
  const chosePath = (path: DatabaseAddVia) => {
    if (editing || pathsChosen.current.has(path)) return
    pathsChosen.current.add(path)
    trackEvent('database_add_path_selected', { via: path })
  }

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

  // Where the attempt stands, for the funnel if the dialog closes on the form.
  const filled = [url, ...Object.values(fields), name].filter((value) =>
    value.trim()
  ).length
  const stage: DatabaseAddStage =
    phase === 'testing'
      ? 'testing'
      : phase === 'saving'
        ? 'saving'
        : check && !check.connected
          ? 'test_failed'
          : check?.connected
            ? 'tested_ok'
            : attempted && problem
              ? 'field_error'
              : filled > 0
                ? 'filling'
                : 'untouched'
  const latest = useRef({ stage, via })
  latest.current = { stage, via }
  const addedRef = useRef(false)
  useEffect(
    () => () => {
      if (!attempt || addedRef.current || attempt.switching) return
      abandonAttempt(attempt, latest.current.via, latest.current.stage)
    },
    [attempt]
  )

  useEffect(() => {
    if (problem || check?.connected)
      errorRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [problem, check?.connected])

  const changeUrl = (value: string) => {
    setUrl(value)
    setCheck(null)
    if (value.trim()) chosePath('url')
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
    chosePath('fields')
  }
  const changeAdvanced = (next: Advanced) => {
    setAdvanced(next)
    setCheck(null)
  }

  /** The form to send, or null when it can't go yet (the reason shows at the top). */
  const ready = (): ConfigureFormData | null => {
    setAttempted(true)
    const badName = Boolean(nameError(name, taken))
    if (!connection || badName) {
      if (!editing)
        for (const [field, error_class] of fieldErrors(url, fields, badName))
          trackEvent('database_add_field_error', { field, error_class })
      return null
    }
    return { ...connection, name: name.trim() || defaultName }
  }

  // An edit tests under the saved name, so the saved password fills a blank one.
  const runTest = async (data: ConfigureFormData): Promise<Check> => {
    if (!editing)
      trackEvent('database_add_test_started', { engine: data.engine, via })
    const result = editing
      ? await configure.testConnection(editing.name, data)
      : await configure.testConnection(data.name, data, via)
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
    const errorClass = next.connected
      ? null
      : userErrorClass(result?.code, result?.category, next.message)
    if (errorClass)
      trackEvent('user_error_shown', {
        surface: editing ? 'edit_database' : 'add_database',
        error_class: errorClass,
      })
    if (attempt && !editing)
      onceForAttempt(attempt, `test-${next.connected}`, () =>
        trackEvent('database_add_test_result', {
          ok: next.connected,
          error_class: errorClass,
          via,
        })
      )
    return next
  }

  const fail = (caught: unknown, fallback: string) => {
    setPhase(null)
    const message = caught instanceof Error ? caught.message : fallback
    setCheck({ connected: false, message })
    trackEvent('user_error_shown', {
      surface: editing ? 'edit_database' : 'add_database',
      error_class: userErrorClass(message),
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

  // Tests, saves, then opens the database's page on the turn-on dialog, whose checks say
  // what to change when query statistics aren't readable yet.
  const add = async () => {
    const data = ready()
    if (!data) return
    setPhase('saving')
    let persisted: boolean | null = null
    try {
      const checked = await runTest(data)
      if (!checked.connected) {
        setPhase(null)
        return
      }
      persisted = (await configure.addTarget(data, via))?.persisted ?? null
      if (taken.length === 0) await configure.setDefaultTarget(data.name)
    } catch (caught) {
      fail(caught, "The database wasn't added.")
      return
    }
    if (taken.length === 0)
      trackEventOnce(
        'onboarding-done-add-database',
        'onboarding_step_completed',
        {
          step: 'add-database',
        }
      )
    addedRef.current = true
    if (attempt) handOffAddFlow(data.name, { attempt, via })
    const open = () => void opened(data.name)
    if (data.password && persisted === false) {
      setPhase(null)
      setSessionOnly(() => open)
      return
    }
    open()
  }

  const opened = async (target: string) => {
    startBootstrapRun(target)
    // Monitoring turns on from the turn-on dialog, after its checks and the EXPLAIN ANALYZE
    // warning.
    selectGlobalTarget(target)
    await queryClient.refetchQueries({ queryKey: ['status'] })
    notifyDatabasesChanged()
    void navigate({ to: '/database', search: { target, panel: 'enable' } })
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
      {/* Masked in session replays: the form holds hosts, users and database names. */}
      <Scrollable rootClassName="min-h-0" className="ph-mask px-6 pt-2 pb-5">
        {sessionOnly && (
          <div className="mt-2 mb-3" data-testid="password-session-only">
            <Alert
              variant="warning"
              modifier="ghost"
              icon="alert"
              label="Your OS keyring isn't available, so RDST keeps this password only until you close the app. You'll need to enter it again next time."
            />
          </div>
        )}
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
              <CaptureNotice capture={capture} editing={Boolean(editing)} />
            )}
          </div>
        )}
        {!editing && (
          <>
            {onAgent && (
              <div className="mt-2 mb-4">
                <AgentConnectOption
                  onPick={() => {
                    if (attempt) attempt.switching = true
                    onAgent()
                  }}
                />
              </div>
            )}
            <Text
              as="p"
              level="caption"
              className="mt-2 mb-1.5 text-content-layout-3"
            >
              Import from
            </Text>
            <SourceList
              onPick={(tab) => {
                if (attempt) attempt.switching = true
                onImport(tab)
              }}
            />
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
        {!editing && (
          <div className="mt-5">
            <DataLine />
          </div>
        )}
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
        {sessionOnly ? (
          <Button
            variant="primary"
            modifier="solid"
            label="Continue"
            onClick={sessionOnly}
          />
        ) : (
          <>
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
          </>
        )}
      </div>
    </>
  )
}

type Importing = AddTab | 'agent' | null

/** The page the dialog opened over, as a route name: never a database or query. */
const entryOf = (path: string) => path.split('/')[1] || 'home'

const importingFor = (request: AddRequest): Importing =>
  request === 'form' ? null : request

const entryFor = (request: AddRequest): AddDatabaseEntry =>
  request === 'form' ? 'form' : request === 'agent' ? 'agent' : 'provider'

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
  const [attempt, setAttempt] = useState(newAddAttempt)
  // Adding the first database is step 2 of getting started. Held while open, so the strip
  // stays through the add that ends the step.
  const status = useSystemStatus()
  const [firstDatabase, setFirstDatabase] = useState(false)
  if (request !== seen) {
    setSeen(request)
    if (request) {
      setOpening((count) => count + 1)
      setAttempt(newAddAttempt())
      setImporting(importingFor(request))
      setStep('source')
      setFirstDatabase(false)
    }
  }
  if (request && !edit && status.data?.targets.length === 0 && !firstDatabase)
    setFirstDatabase(true)
  if (edit && edit !== lastEdit) setLastEdit(edit)
  const editing = edit ?? (open ? null : lastEdit)
  useEffect(() => {
    if (opening > 0 && seen)
      trackEvent('database_add_opened', {
        via_entry: entryOf(window.location.pathname),
        entry: entryFor(seen),
      })
  }, [opening, seen])
  // The view that mounted after a switch between paths carries the attempt on.
  useEffect(() => {
    if (importing !== undefined) attempt.switching = false
  }, [importing, attempt])
  const importFrom = (tab: AddTab) => {
    trackEvent('database_add_path_selected', { via: 'provider' })
    setImporting(tab)
  }
  const withAgent = () => {
    trackEvent('database_add_path_selected', { via: 'agent' })
    setImporting('agent')
  }
  const title = editing
    ? 'Edit database'
    : importing === 'agent'
      ? 'Connect with your coding agent'
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
          tone="quiet"
          onEscapeKeyDown={(event) => {
            // Escape in a provider view goes back to the form, as Back does.
            if (editing || !importing || step !== 'source') return
            event.preventDefault()
            attempt.switching = true
            setImporting(null)
          }}
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
                onClick={() => {
                  attempt.switching = true
                  setImporting(null)
                }}
              />
            )}
            <ModalTitle>{title}</ModalTitle>
          </div>
          {firstDatabase && !editing && (
            <div className="shrink-0 px-6 pt-2">
              <GetStartedStrip step="add-database" />
            </div>
          )}
          {editing ? (
            <ConnectionForm
              key={editing.name}
              onImport={setImporting}
              editing={editing}
              onSaved={onSaved}
            />
          ) : importing === 'agent' ? (
            <AgentConnect
              attempt={attempt}
              onAddByHand={() => {
                setImporting(null)
                // Leaving the agent path lands on the connection string, the next way in.
                window.setTimeout(() =>
                  document
                    .querySelector<HTMLInputElement>(
                      '[data-testid="add-database"] input[name="connection_string"]'
                    )
                    ?.focus()
                )
              }}
            />
          ) : importing ? (
            <ProviderImport
              key={opening}
              initialTab={importing}
              onClose={onClose}
              onStepChange={setStep}
            />
          ) : (
            <ConnectionForm
              onImport={importFrom}
              onAgent={withAgent}
              attempt={attempt}
            />
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
