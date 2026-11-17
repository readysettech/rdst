import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SettingsRow } from '../features/settings/SettingsGroup'
import { syncAnalyticsConsent } from '../lib/analytics'
import { fetchTelemetrySettings, setTelemetryConsent } from '../lib/api'
import { telemetrySettingsQueryKey } from '../lib/diagnosticsConsent'

export function TelemetrySettingsControl() {
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: telemetrySettingsQueryKey,
    queryFn: fetchTelemetrySettings,
  })
  const update = useMutation({
    mutationFn: setTelemetryConsent,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: telemetrySettingsQueryKey })
    },
    onSuccess: async (data) => {
      void syncAnalyticsConsent(data.enabled)
      // A read started while the save was in flight would otherwise land on top of it.
      await queryClient.cancelQueries({ queryKey: telemetrySettingsQueryKey })
      queryClient.setQueryData(telemetrySettingsQueryKey, data)
    },
  })
  const data = settings.data
  // Where no opt-in is required, diagnostics are on until someone turns them off.
  const enabled =
    data?.consent === 'granted' ||
    (data?.consent === 'pending' && !data.consent_required)
  const forcedOff = data?.forced_off_reason != null

  return (
    <>
      <SettingsRow
        title="Share diagnostics"
        line="Sends usage and error diagnostics to PostHog, linked to your Readyset account when you're signed in. Selected events can trigger internal Slack notifications. Raw SQL, query results, and database names are not included."
      >
        <BaseInputSwitch
          name="routine-telemetry"
          aria-label="Share diagnostics"
          checked={enabled}
          disabled={settings.isLoading || settings.isError || update.isPending}
          onCheckedChange={(checked) => {
            update.mutate(checked ? 'granted' : 'denied')
          }}
        />
      </SettingsRow>
      {forcedOff ? (
        <Text as="p" level="caption" className="mt-1.5 text-content-layout-3">
          Diagnostics are currently disabled by this installation's environment.
          Your stored preference takes effect if that override is removed.
        </Text>
      ) : null}
      {settings.isError ? (
        <div className="mt-1.5 flex items-center gap-3">
          <Text level="caption" className="text-content-negative-soft">
            RDST could not load the saved diagnostics preference.
          </Text>
          <Button
            modifier="ghost"
            variant="negative"
            size="small"
            label="Disable diagnostics"
            loading={update.isPending}
            onClick={() => update.mutate('denied')}
          />
        </div>
      ) : update.isError ? (
        <Text
          as="p"
          level="caption"
          className="mt-1.5 text-content-negative-soft"
        >
          RDST could not update the diagnostics preference. Diagnostics remain
          off unless a prior opt-in is still active.
        </Text>
      ) : null}
    </>
  )
}
