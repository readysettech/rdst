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
}: {
  enabled?: boolean
  /** Settings-page rendering: one quiet card per connection state. */
  compact?: boolean
  /** Where the compact signed-out card sends the user to actually sign in. */
  onSignIn?: () => void
}) {
  return (
    <ProviderConnectionPanel
      slug="supabase"
      label="Supabase"
      Logo={SupabaseLogo}
      client={client}
      pitch="Discover your Supabase projects"
      blurb="Sign in so we can list your Supabase projects and add them as targets."
      connectedLine={(status) => {
        const names = (status.organizations ?? [])
          .map((org) => org.name || org.slug)
          .filter(Boolean)
        return `Connected to Supabase with OAuth${
          names.length > 0 ? ` - ${names.join(', ')}` : ''
        }`
      }}
      rejectedTitle="Supabase rejected the saved credentials"
      enabled={enabled}
      compact={compact}
      onSignIn={onSignIn}
    />
  )
}
