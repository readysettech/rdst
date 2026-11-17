// The Store build's two questions, asked together once, the second time someone opens RDST:
// diagnostics and starting at sign-in. Both start ticked and take effect only on Save;
// skipping or closing leaves them off. Settings (and the tray, for Start at login) changes them later.
import { BaseInputCheckboxRow } from '@rs/ui-new/base-input-checkbox'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { tourPending } from '../features/onboarding/onboardingState'
import { syncAnalyticsConsent } from '../lib/analytics'
import { fetchTelemetrySettings, setTelemetryConsent } from '../lib/api'
import { thisComputer } from '../lib/desktop'
import { telemetrySettingsQueryKey } from '../lib/diagnosticsConsent'
import { useStartAtLogin } from '../lib/startAtLogin'

// How long to wait before looking for another first-run dialog, and between looks: one can
// open in the same moment, its content still loading.
const RECHECK_MS = 400

/** The tour, the add-database prompt (open through the URL while its dialog loads) or any
 * other dialog is up. */
function otherDialogUp(): boolean {
  return (
    tourPending() ||
    new URLSearchParams(window.location.search).has('add') ||
    document.querySelector('[role="dialog"]') !== null
  )
}

export function ConsentChoicesDialog() {
  const client = useQueryClient()
  const desktop = window.rdstDesktop
  const opens = useQuery({
    queryKey: ['desktop', 'window-opens'],
    queryFn: () => desktop?.getWindowOpens?.() ?? 0,
    enabled: desktop?.getWindowOpens !== undefined,
  })
  const secondOpen = (opens.data ?? 0) >= 2
  const telemetry = useQuery({
    queryKey: telemetrySettingsQueryKey,
    queryFn: fetchTelemetrySettings,
    enabled: secondOpen,
  })
  const login = useStartAtLogin()
  const settings = telemetry.data
  const askDiagnostics =
    settings?.consent_required === true &&
    settings.consent === 'pending' &&
    settings.forced_off_reason == null
  const askLogin = login.status.data?.ask === true

  const [open, setOpen] = useState(false)
  const [share, setShare] = useState(true)
  const [startAtLogin, setStartAtLogin] = useState(true)
  // Opens once per load, when both answers are known and every other dialog has closed.
  const decided = useRef(false)
  useEffect(() => {
    if (decided.current || !secondOpen || !settings || !login.status.data)
      return
    if (!askDiagnostics && !askLogin) {
      decided.current = true
      return
    }
    let timer: ReturnType<typeof setTimeout>
    const tryOpen = () => {
      if (otherDialogUp()) {
        timer = setTimeout(tryOpen, RECHECK_MS)
        return
      }
      decided.current = true
      setOpen(true)
    }
    timer = setTimeout(tryOpen, RECHECK_MS)
    return () => clearTimeout(timer)
  }, [secondOpen, settings, login.status.data, askDiagnostics, askLogin])

  const save = useMutation({
    mutationFn: async (choice: { share: boolean; startAtLogin: boolean }) => {
      if (askDiagnostics) {
        await client.cancelQueries({ queryKey: telemetrySettingsQueryKey })
        const data = await setTelemetryConsent(
          choice.share ? 'granted' : 'denied'
        )
        void syncAnalyticsConsent(data.enabled)
        await client.cancelQueries({ queryKey: telemetrySettingsQueryKey })
        client.setQueryData(telemetrySettingsQueryKey, data)
      }
      if (askLogin) await login.update.mutateAsync(choice.startAtLogin)
    },
    onSuccess: () => setOpen(false),
  })

  const title =
    askDiagnostics && askLogin
      ? 'Diagnostics and startup'
      : askDiagnostics
        ? 'Share diagnostics?'
        : 'Start RDST when you sign in?'
  return (
    <ConfirmDialog
      isOpen={open}
      onClose={() => save.mutate({ share: false, startAtLogin: false })}
      onConfirm={() => save.mutate({ share, startAtLogin })}
      tone="quiet"
      title={title}
      subtitle={
        askDiagnostics && askLogin
          ? 'RDST asks once, and Settings changes these later. Skip leaves both off.'
          : 'RDST asks once, and Settings changes it later. Skip leaves it off.'
      }
      confirmLabel="Save"
      confirmVariant="primary"
      cancelLabel="Skip"
      loading={save.isPending}
      blockCloseWhileLoading
      note={save.isError ? "Couldn't save. Try again." : undefined}
    >
      <div className="flex flex-col gap-2" data-testid="consent-choices">
        {askDiagnostics && (
          <Choice label="Share diagnostics" checked={share} onChange={setShare}>
            Usage and crash reports to Readyset through PostHog: the features
            you use, errors, system details, query hashes and a random install
            ID, linked to your account when you're signed in. Some events notify
            Readyset's team in Slack. Never your SQL, query results or database
            names.
          </Choice>
        )}
        {askLogin && (
          <Choice
            label="Start RDST when you sign in"
            checked={startAtLogin}
            onChange={setStartAtLogin}
          >
            RDST checks your databases only while it runs. Starting it in the
            tray when you sign in to {thisComputer()} keeps checks and alerts
            going after a restart.
          </Choice>
        )}
      </div>
    </ConfirmDialog>
  )
}

function Choice({
  label,
  checked,
  onChange,
  children,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
}) {
  const detail = useId()
  return (
    <BaseInputCheckboxRow
      aria-label={label}
      aria-describedby={detail}
      checked={checked}
      onCheckedChange={(value) => onChange(value === true)}
      className="items-start rounded-panel border border-border-layout-2 px-4 py-3"
    >
      <span className="min-w-0 flex-1">
        <Text
          as="span"
          level="label-medium"
          className="block text-content-layout-1"
        >
          {label}
        </Text>
        <span id={detail} className="mt-1 block">
          <Text
            as="span"
            level="body-small"
            className="block text-content-layout-2"
          >
            {children}
          </Text>
        </span>
      </span>
    </BaseInputCheckboxRow>
  )
}
