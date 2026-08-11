import { createFileRoute } from '@tanstack/react-router'
import { parseAddTab } from '../components/configure/addTabs'
import {
  type ConfigureSearch,
  SettingsPage,
} from '../features/settings/SettingsPage'

export const Route = createFileRoute('/configure')({
  validateSearch: (search: Record<string, unknown>): ConfigureSearch => ({
    edit: typeof search.edit === 'string' ? search.edit : undefined,
    section: search.section === 'ai' ? 'ai' : undefined,
    panel:
      search.panel === 'connections' ||
      search.panel === 'ai' ||
      search.panel === 'privacy' ||
      search.panel === 'developer'
        ? search.panel
        : undefined,
    returnTo: typeof search.returnTo === 'string' ? search.returnTo : undefined,
    add: parseAddTab(search.add),
  }),
  component: ConfigureRoute,
})

function ConfigureRoute() {
  return <SettingsPage search={Route.useSearch()} />
}
