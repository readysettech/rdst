import {
  type FleetNeonStatus,
  fetchFleetNeonStatus,
  fleetNeonLogout,
  setFleetNeonKey,
} from '../../lib/useFleet'
import {
  ProviderConnectionPanel,
  type ProviderPanelClient,
} from '../providers/ProviderConnectionPanel'
import { NeonLogo } from '../providers/ProviderLogos'
import { ProviderTokenForm } from '../providers/ProviderTokenForm'

const client: ProviderPanelClient<FleetNeonStatus> = {
  fetchStatus: fetchFleetNeonStatus,
  logout: fleetNeonLogout,
}

/**
 * Neon has no browser sign-in: an API key is the whole credential, so the
 * signed-out panel is the key field itself rather than a button that opens a
 * tab.
 */
export function NeonConnectionPanel({
  enabled = true,
  compact = false,
  onSignIn,
}: {
  enabled?: boolean
  /** Settings-page rendering: one quiet card per connection state. */
  compact?: boolean
  /** Where the compact signed-out card sends the user to enter a key. */
  onSignIn?: () => void
}) {
  return (
    <ProviderConnectionPanel
      slug="neon"
      label="Neon"
      Logo={NeonLogo}
      client={client}
      pitch="Discover your Neon projects"
      blurb="Paste a Neon API key - create one under Account settings > API keys in the Neon console."
      connectedLine={() => 'Connected to Neon with an API key'}
      rejectedTitle="Neon rejected the saved API key"
      connectLabel="Connect"
      credentialSlot={({ onSaved }) => (
        <ProviderTokenForm
          name="neon-api-key"
          placeholder="napi_..."
          submitLabel="Connect"
          submitBusyLabel="Connecting..."
          rejectedMessage="That API key was rejected by Neon. Check it and try again."
          save={setFleetNeonKey}
          onSaved={onSaved}
        />
      )}
      enabled={enabled}
      compact={compact}
      onSignIn={onSignIn}
    />
  )
}
