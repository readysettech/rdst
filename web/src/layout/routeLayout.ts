import { useMatches } from '@tanstack/react-router'

/**
 * How the shell frames a route. `page`: the route renders its own PageBar and
 * PageContainer inside the scrolling canvas. `split`: the route renders a
 * TwoPane that fills the shell, so the page never scrolls. `bare`: no shell
 * at all (sign-in). `legacy`: the shell adds a title bar and the content axis
 * around a page that still carries its own heading.
 */
export type RouteLayout = 'legacy' | 'page' | 'split' | 'bare'

declare module '@tanstack/react-router' {
  interface StaticDataRouteOption {
    layout?: RouteLayout
  }
}

export function useRouteLayout(): RouteLayout {
  const matches = useMatches()
  for (let i = matches.length - 1; i >= 0; i--) {
    const layout = matches[i].staticData?.layout
    if (layout) return layout
  }
  return 'legacy'
}
