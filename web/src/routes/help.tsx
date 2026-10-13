import { createFileRoute } from '@tanstack/react-router'
// The page lives in the route-ignored `-help-page` sibling so the route's
// `component:` stays a local wrapper the code-splitter can move, and tests
// render the page directly.
import { HelpPage } from './-help-page'

export const Route = createFileRoute('/help')({
  staticData: { layout: 'page' },
  component: HelpRoute,
})

function HelpRoute() {
  return <HelpPage />
}
