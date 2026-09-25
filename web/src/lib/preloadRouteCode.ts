// The live API holds several long-lived streams, and a slow API call can hold another
// connection for seconds, so the browser's six per-origin HTTP/1.1 connections can all
// be busy at the moment someone clicks a link. A route whose code is still to be
// fetched then waits behind them while the old page stays on screen. Fetching every
// route's code once, as the app starts and before any stream opens, means a click
// never waits on the network for code.

interface RouteChunkLoader<Route> {
  /** The router's route map (an interface of route ids, so typed loosely here). */
  routesById: object
  loadRouteChunk: (route: Route) => Promise<void> | undefined
}

/** Starts loading every route's code; failures are left for navigation to report. */
export function preloadRouteCode<Route>(router: RouteChunkLoader<Route>) {
  for (const route of Object.values(router.routesById) as Route[])
    void router.loadRouteChunk(route)?.catch(() => undefined)
}
