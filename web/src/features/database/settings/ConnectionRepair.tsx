// Fix connection: the dialog names the host and the likely cause, the saved fields can
// be changed in place with the password kept unless typed, and "Check again" saves and
// tries the database once more.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Skeleton } from '@rs/ui-new/skeleton'
import { useEffect, useId, useState } from 'react'
import { useConfigure } from '@/lib/useConfigure'
import type { ConfigureTargetDetail } from '@/types/configure'
import { DialogShell } from '../../../components/DialogShell'

const ENGINE_NAME: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

/** The warning line: the host and the likely cause, in one sentence or two. */
export function connectionWarning(
  detail: Pick<ConfigureTargetDetail, 'host' | 'port' | 'engine'> | null,
  reason: string | null
): string {
  const where = detail ? `${detail.host}:${detail.port}` : 'The database'
  if (reason && /refus/i.test(reason)) {
    const engine = detail ? (ENGINE_NAME[detail.engine] ?? detail.engine) : null
    return `${where} refused the connection.${engine ? ` Is ${engine} running?` : ''}`
  }
  return reason ? `${where}: ${reason}` : `${where} can't be reached.`
}

interface Fields {
  host: string
  port: string
  database: string
  user: string
  password: string
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  className,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: 'text' | 'password'
  placeholder?: string
  className?: string
}) {
  const id = useId()
  return (
    <div className={className}>
      <label
        htmlFor={id}
        className="mb-1 block text-caption text-content-layout-3"
      >
        {label}
      </label>
      <BaseInputText
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-8.5 bg-surface-layout-2 text-body-small"
      />
    </div>
  )
}

export function ConnectionRepair({
  target,
  open,
  onClose,
  reason,
  onSaved,
}: {
  target: string
  open: boolean
  onClose: () => void
  /** The collector's last error, when it has one. */
  reason: string | null
  /** The connection was saved; check the database again. */
  onSaved: () => void
}) {
  const configure = useConfigure()
  const { getTarget } = configure
  const [detail, setDetail] = useState<ConfigureTargetDetail | null>(null)
  const [fields, setFields] = useState<Fields | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setDetail(null)
      setFields(null)
      setLoadFailed(false)
      setSaveError(null)
      return
    }
    let live = true
    void getTarget(target).then((loaded) => {
      if (!live) return
      if (!loaded) {
        setLoadFailed(true)
        return
      }
      setDetail(loaded)
      setFields({
        host: loaded.host,
        port: String(loaded.port),
        database: loaded.database,
        user: loaded.user,
        password: '',
      })
    })
    return () => {
      live = false
    }
  }, [open, getTarget, target])

  const set = (name: keyof Fields) => (value: string) =>
    setFields((current) => (current ? { ...current, [name]: value } : current))

  const checkAgain = async () => {
    if (!detail || !fields) return
    setSaving(true)
    setSaveError(null)
    try {
      await configure.updateTarget(target, {
        ...detail,
        host: fields.host,
        port: Number(fields.port) || detail.port,
        database: fields.database,
        user: fields.user,
        password: fields.password || undefined,
      })
      onSaved()
      onClose()
    } catch (error) {
      setSaveError(
        error instanceof Error ? error.message : "The connection wasn't saved."
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      title={`Can't reach ${target}`}
      testId="connection-repair"
      note="Monitoring resumes by itself once it connects."
      action={
        <Button
          variant="primary"
          modifier="solid"
          label="Check again"
          loading={saving}
          disabled={!fields}
          onClick={() => void checkAgain()}
        />
      }
    >
      <Alert
        variant="warning"
        tone="quiet"
        icon="alert"
        className="mt-1"
        label={connectionWarning(detail, reason)}
      />
      {loadFailed ? (
        <Alert
          variant="negative"
          tone="quiet"
          icon="alert"
          className="mt-3"
          label="RDST couldn't load this connection. Edit it in Settings."
        />
      ) : !fields ? (
        <div className="mt-4 grid grid-cols-2 gap-3" aria-busy="true">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <Field label="Host" value={fields.host} onChange={set('host')} />
          <Field label="Port" value={fields.port} onChange={set('port')} />
          <Field
            label="Database"
            value={fields.database}
            onChange={set('database')}
          />
          <Field label="User" value={fields.user} onChange={set('user')} />
          <Field
            label="Password"
            type="password"
            value={fields.password}
            onChange={set('password')}
            placeholder="Leave blank to keep it"
            className="col-span-2"
          />
        </div>
      )}
      {reason && (
        <Disclosure title="Details" className="mt-3">
          <pre className="mt-2 rounded-control bg-surface-layout-2 px-3.5 py-3 text-mono-medium whitespace-pre-wrap text-content-layout-1">
            {reason}
          </pre>
        </Disclosure>
      )}
      {saveError && (
        <Alert
          variant="negative"
          tone="quiet"
          icon="alert"
          className="mt-3"
          label={saveError}
        />
      )}
    </DialogShell>
  )
}
