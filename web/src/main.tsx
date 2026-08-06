import domMax from '@rs/ui-new/dom-max'
import LazyMotion from '@rs/ui-new/lazy-motion'
import { Toaster } from '@rs/ui-new/toaster'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRouter, RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { initAnalytics } from './lib/analytics'
import { routeTree } from './routeTree.gen'

import './style.css'

initAnalytics()

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const queryClient = new QueryClient()

const rootElement = document.getElementById('root')!
if (!rootElement.innerHTML) {
  const root = createRoot(rootElement)
  root.render(
    <StrictMode>
      <LazyMotion features={domMax} strict>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
          <Toaster />
        </QueryClientProvider>
      </LazyMotion>
    </StrictMode>
  )
}
