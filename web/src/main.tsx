import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider, createRouter } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LazyMotion from '@rs/ui-new/lazy-motion';
import domMax from '@rs/ui-new/dom-max';
import { Toaster } from '@rs/ui-new/toaster';

import { routeTree } from './routeTree.gen';

import './style.css';

const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}

const queryClient = new QueryClient();

const rootElement = document.getElementById('root')!;
if (!rootElement.innerHTML) {
  const root = createRoot(rootElement);
  root.render(
    <StrictMode>
      <LazyMotion features={domMax} strict>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
          <Toaster />
        </QueryClientProvider>
      </LazyMotion>
    </StrictMode>,
  );
}
