import {
  type FleetSupabaseStatus,
  fetchFleetSupabaseLogin,
  fetchFleetSupabaseStatus,
  fleetSupabaseLogout,
  startFleetSupabaseLogin,
} from '../../lib/useFleet'
import {
  ProviderConnectionPanel,
  type ProviderPanelClient,
} from '../providers/ProviderConnectionPanel'
import { SupabaseLogo } from '../providers/ProviderLogos'

const client: ProviderPanelClient<FleetSupabaseStatus> = {
  fetchStatus: fetchFleetSupabaseStatus,
  startLogin: startFleetSupabaseLogin,
  pollLogin: fetchFleetSupabaseLogin,
  logout: fleetSupabaseLogout,
}

export function SupabaseConnectionPanel({
  enabled = true,
  compact = false,
  onSignIn,
  actions,
}: {
  enabled?: boolean
  /** Settings-page rendering: one quiet card per connection state. */
  compact?: boolean
  /** Where the compact signed-out card sends the user to actually sign in. */
  onSignIn?: () => void
  /** The host dialog's footer, where the panel's buttons render. */
  actions?: HTMLElement | null
}) {
  return (
    <ProviderConnectionPanel
      slug="supabase"
      label="Supabase"
      Logo={SupabaseLogo}
      client={client}
      pitch="Discover your Supabase projects"
      blurb="Sign in so we can list the databases in your Supabase projects."
      connectedLine={(status) => {
        const names = (status.organizations ?? [])
          .map((org) => org.name || org.slug)
          .filter(Boolean)
        return `Signed in to Supabase${
          names.length > 0 ? ` (${names.join(', ')})` : ''
        }`
      }}
      rejectedTitle="Supabase rejected the saved credentials"
      enabled={enabled}
      compact={compact}
      onSignIn={onSignIn}
      actions={actions}
    />
  )
}
