import {
  type FleetDigitaloceanStatus,
  fetchFleetDigitaloceanLogin,
  fetchFleetDigitaloceanStatus,
  fleetDigitaloceanLogout,
  startFleetDigitaloceanLogin,
} from '../../lib/useFleet'
import {
  ProviderConnectionPanel,
  type ProviderPanelClient,
} from '../providers/ProviderConnectionPanel'
import { DigitalOceanLogo } from '../providers/ProviderLogos'

const client: ProviderPanelClient<FleetDigitaloceanStatus> = {
  fetchStatus: fetchFleetDigitaloceanStatus,
  startLogin: startFleetDigitaloceanLogin,
  pollLogin: fetchFleetDigitaloceanLogin,
  logout: fleetDigitaloceanLogout,
}

/**
 * DigitalOcean authenticates through the Readyset OAuth broker alone: browser
 * sign-in is the whole credential, so a failed start is reported as a failure
 * rather than falling back to a token field.
 */
export function DigitalOceanConnectionPanel({
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
      slug="digitalocean"
      label="DigitalOcean"
      Logo={DigitalOceanLogo}
      client={client}
      pitch="Import your managed PostgreSQL and MySQL databases"
      blurb="Sign in so we can list your managed PostgreSQL and MySQL databases."
      connectedLine={() => 'Signed in to DigitalOcean'}
      rejectedTitle="DigitalOcean rejected the saved credentials"
      enabled={enabled}
      compact={compact}
      onSignIn={onSignIn}
      actions={actions}
    />
  )
}
