// When RDST can't reach a database, its saved connection is edited and tested right in
// the monitoring dialog. Saving goes through the configure API, then the prerequisites
// are checked again once, so there's no waiting on a retry loop.
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { ConfigureForm } from '@/components/configure/ConfigureForm'
import { useConfigure } from '@/lib/useConfigure'
import type {
  ConfigureFormData,
  ConfigureTargetDetail,
} from '@/types/configure'

export function ConnectionRepair({
  target,
  editing,
  onEditingChange,
  onSaved,
}: {
  target: string
  editing: boolean
  onEditingChange: (editing: boolean) => void
  /** The connection was saved; check the database again. */
  onSaved: () => void
}) {
  const configure = useConfigure()
  const { getTarget } = configure
  const [detail, setDetail] = useState<ConfigureTargetDetail | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!editing || detail) return
    let live = true
    void getTarget(target).then((loaded) => {
      if (!live) return
      if (loaded) setDetail(loaded)
      else setLoadFailed(true)
    })
    return () => {
      live = false
    }
  }, [editing, detail, getTarget, target])

  if (!editing)
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-panel border border-border-layout-1 bg-surface-layout-1 px-4 py-3">
        <Text as="p" level="body-small" className="text-content-layout-2">
          Wrong host, port or password? Fix the connection here.
        </Text>
        <Button
          size="small"
          modifier="outline"
          icon="edit"
          iconPosition="left"
          label="Edit connection"
          onClick={() => onEditingChange(true)}
        />
      </div>
    )

  const test = async (data: ConfigureFormData) => {
    setTesting(true)
    try {
      return await configure.testConnection(target, data)
    } finally {
      setTesting(false)
    }
  }

  const save = async (data: ConfigureFormData) => {
    setSaving(true)
    setSaveError(null)
    try {
      await configure.updateTarget(target, data)
      setDetail(null)
      onEditingChange(false)
      onSaved()
    } catch (error) {
      setSaveError(
        error instanceof Error ? error.message : "The connection wasn't saved."
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      aria-label={`Connection details for ${target}`}
      data-testid="connection-repair"
      className="space-y-3"
    >
      <div className="space-y-0.5">
        <Text as="h3" level="label-medium" className="text-content-layout-1">
          Fix the connection
        </Text>
        <Text as="p" level="caption" className="text-content-layout-3">
          Test the connection, then save it. RDST checks the database again
          right after. Leave the password empty to keep the saved one.
        </Text>
      </div>
      {loadFailed ? (
        <Alert
          variant="warning"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label="RDST couldn't load this connection. Edit it in Settings."
        />
      ) : !detail ? (
        <div className="space-y-2" aria-busy="true">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-2/3" />
        </div>
      ) : (
        <ConfigureForm
          key={target}
          initialData={detail}
          showHeader={false}
          onTest={test}
          onSubmit={(data) => void save(data)}
          onCancel={() => {
            setDetail(null)
            onEditingChange(false)
          }}
          isTesting={testing}
          isLoading={saving}
          testResult={configure.connectionTestResult}
          submitLabel="Save and check again"
        />
      )}
      {saveError ? (
        <Alert
          variant="negative"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label={saveError}
        />
      ) : null}
    </section>
  )
}
