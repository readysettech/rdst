import domMax from '@rs/ui-new/dom-max'
import LazyMotion from '@rs/ui-new/lazy-motion'
import { MotionConfig } from '@rs/ui-new/motion'
import { Toaster } from '@rs/ui-new/toaster'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRouter, RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { installAnalysisRunInvalidation } from './features/queries/results/analysisRunInvalidation'
import { initAnalytics } from './lib/analytics'
import { fetchEnvRequirements } from './lib/api'
import {
  installBackendReachability,
  keepDataWhileDown,
  retryQuery,
} from './lib/backendReachability'
import { preloadRouteCode } from './lib/preloadRouteCode'
import { routeTree } from './routeTree.gen'

import './style.css'

installBackendReachability()

void fetchEnvRequirements()
  .then(({ telemetry_enabled }) => initAnalytics(telemetry_enabled))
  // Fail closed: if the local preference cannot be read, do not initialize
  // browser analytics and risk overriding an explicit opt-out.
  .catch(() => undefined)

const router = createRouter({ routeTree })
// Built chunks only: the dev server serves every module on its own, and fetching all
// of them up front would slow each page load there.
if (import.meta.env.PROD) preloadRouteCode(router)

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// React Query's default (three retries with exponential backoff) held every
// list behind its loading state for ~7s before the error branch could run, so
// a dead endpoint looked like a slow one. One retry covers a transient blip
// and still surfaces a real failure in about two seconds. [F-06] While the
// backend itself is down, queries wait for it and keep the data they had.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: retryQuery,
      retryDelay: 800,
      structuralSharing: keepDataWhileDown,
    },
  },
})
installAnalysisRunInvalidation(queryClient)

const rootElement = document.getElementById('root')!
if (!rootElement.innerHTML) {
  const root = createRoot(rootElement)
  root.render(
    <StrictMode>
      <LazyMotion features={domMax} strict>
        {/* Every m.* entrance in the product reads the user's motion
            preference from here, so no call site has to remember to. */}
        <MotionConfig reducedMotion="user">
          <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
            <Toaster />
          </QueryClientProvider>
        </MotionConfig>
      </LazyMotion>
    </StrictMode>
  )
}
