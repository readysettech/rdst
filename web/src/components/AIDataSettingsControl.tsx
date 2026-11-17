// The AI switch in builds that send nothing to AI until allowed (the Microsoft Store build).
// Turning it on asks with what AI sees; turning it off confirms what stops.
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { SettingsRow } from '../features/settings/SettingsGroup'
import { useAiConsent } from '../lib/aiConsent'
import { fetchTelemetrySettings } from '../lib/api'
import { telemetrySettingsQueryKey } from '../lib/diagnosticsConsent'
import { AllowAiDialog, TurnOffAiDialog } from './AiConsent'

export function AIDataSettingsControl() {
  const ai = useAiConsent()
  const [allowOpen, setAllowOpen] = useState(false)
  const [offOpen, setOffOpen] = useState(false)
  // The diagnostics setting says whether this build asks first, when this one can't.
  const telemetry = useQuery({
    queryKey: telemetrySettingsQueryKey,
    queryFn: fetchTelemetrySettings,
    enabled: ai.failed,
  })

  // A failed load still offers the way out, unless the build never asks.
  if (ai.failed) {
    if (telemetry.data?.consent_required === false) return null
    return (
      <div className="flex items-center gap-3 border-b border-border-layout-2 py-3.5">
        <Text level="caption" className="flex-1 text-content-negative-soft">
          RDST could not load whether AI is on.
        </Text>
        <Button
          modifier="ghost"
          variant="negative"
          size="small"
          label="Turn off AI"
          loading={ai.deciding}
          onClick={() => void ai.decide('denied').catch(() => {})}
        />
      </div>
    )
  }
  if (!ai.required) return null

  return (
    <>
      <SettingsRow
        title="AI"
        line={
          ai.off
            ? 'Off. Charts and alerts for connections, stuck queries and locks keep running; slow-query alerts, fixes, Ask and analysis are off.'
            : 'Sends SQL, schemas, statistics and sample values to AI models to find problems and write fixes.'
        }
      >
        <BaseInputSwitch
          name="ai"
          aria-label="AI"
          checked={!ai.off}
          disabled={ai.loading || ai.deciding}
          onCheckedChange={(on) => (on ? setAllowOpen(true) : setOffOpen(true))}
        />
      </SettingsRow>
      <AllowAiDialog isOpen={allowOpen} onClose={() => setAllowOpen(false)} />
      <TurnOffAiDialog isOpen={offOpen} onClose={() => setOffOpen(false)} />
    </>
  )
}
