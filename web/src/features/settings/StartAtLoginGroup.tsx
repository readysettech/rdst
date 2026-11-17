import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { thisComputer } from '../../lib/desktop'
import { useStartAtLogin } from '../../lib/startAtLogin'
import { SettingsGroup, SettingsRow } from './SettingsGroup'

/** The desktop app's Start at login; nothing in a browser. */
export function StartAtLoginGroup() {
  const { status, update } = useStartAtLogin()
  const login = status.data
  if (!login) return null
  return (
    <SettingsGroup id="startup" title="Startup">
      <SettingsRow
        title="Start at login"
        line={
          update.isError
            ? "Couldn't change Start at login. Try again."
            : (login.note ??
              `Starts RDST in the tray when you sign in to ${thisComputer()}. The same switch as Start at login in the tray menu.`)
        }
      >
        <BaseInputSwitch
          name="start-at-login"
          aria-label="Start at login"
          checked={login.enabled}
          disabled={!login.available || update.isPending}
          onCheckedChange={(on) => update.mutate(on)}
        />
      </SettingsRow>
    </SettingsGroup>
  )
}
