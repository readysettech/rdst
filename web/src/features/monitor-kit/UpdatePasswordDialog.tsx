// Sets a monitored database's password from where its connection problem shows, then
// takes a reading so the page reflects the new password right away.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { defaultPasswordEnv } from '@/components/configure/ConfigureForm'
import { setEnvSecret } from '@/lib/api'
import { collectNow } from '@/lib/monitor/api'
import { monitorKeys } from '@/lib/monitor/keys'
import { useConfigure } from '@/lib/useConfigure'
import { ScrollDialog } from './ScrollDialog'

export function UpdatePasswordDialog({
  target,
  open,
  onClose,
}: {
  target: string
  open: boolean
  onClose: () => void
}) {
  const client = useQueryClient()
  const { getTarget } = useConfigure()
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const detail = await getTarget(target)
      const name = detail?.password_env || defaultPasswordEnv(target)
      await setEnvSecret({ name, value: password })
      const reading = await collectNow(target)
      await client.invalidateQueries({ queryKey: ['monitor', target] })
      await client.invalidateQueries({ queryKey: monitorKeys.inboxAll })
      if (reading.ok === false) {
        setError(
          reading.detail || "RDST still can't connect with this password."
        )
        return
      }
      setPassword('')
      onClose()
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Couldn't save the password."
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <ScrollDialog
      open={open}
      onClose={onClose}
      title="Update password"
      description={`The password RDST uses to read ${target}.`}
      footer={
        <>
          <Button label="Cancel" modifier="ghost" onClick={onClose} />
          <Button
            label={saving ? 'Checking' : 'Save and check'}
            disabled={!password || saving}
            onClick={save}
          />
        </>
      }
    >
      <div className="space-y-3">
        <BaseInputText
          name="update-database-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Database password"
          autoComplete="new-password"
          disabled={saving}
        />
        <Text as="p" level="caption" className="text-content-layout-3">
          RDST stores it in your system keychain when one is available.
        </Text>
        {error ? <Alert variant="negative" label={error} /> : null}
      </div>
    </ScrollDialog>
  )
}
