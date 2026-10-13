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
  actions,
}: {
  enabled?: boolean
  /** Settings-page rendering: one quiet card per connection state. */
  compact?: boolean
  /** Where the compact signed-out card sends the user to enter a key. */
  onSignIn?: () => void
  /** The host dialog's footer, where the panel's buttons render. */
  actions?: HTMLElement | null
}) {
  return (
    <ProviderConnectionPanel
      slug="neon"
      label="Neon"
      Logo={NeonLogo}
      client={client}
      pitch="Discover your Neon projects"
      blurb={
        <>
          Paste a Neon API key. Create one in the{' '}
          <a
            href="https://console.neon.tech"
            target="_blank"
            rel="noreferrer"
            className="text-content-primary-soft underline-offset-4 hover:underline"
          >
            Neon console
          </a>{' '}
          under Account settings &gt; API keys.
        </>
      }
      connectedLine={() => 'Connected to Neon with an API key'}
      rejectedTitle="Neon rejected the saved API key"
      connectLabel="Connect"
      credentialSlot={({ onSaved, actions: footer }) => (
        <ProviderTokenForm
          name="neon-api-key"
          label="API key"
          placeholder="napi_..."
          submitLabel="Connect"
          rejectedMessage="That API key was rejected by Neon. Check it and try again."
          save={setFleetNeonKey}
          onSaved={onSaved}
          actions={footer}
        />
      )}
      enabled={enabled}
      compact={compact}
      onSignIn={onSignIn}
      actions={actions}
    />
  )
}
