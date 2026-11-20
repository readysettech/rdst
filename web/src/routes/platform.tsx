import { Text } from '@rs/ui-new/text'
import { createFileRoute } from '@tanstack/react-router'
import { PlatformPage } from '../features/platform/PlatformPage'
import { isContainerRuntime } from '../lib/desktop'

export const Route = createFileRoute('/platform')({
  staticData: { layout: 'page' },
  component: PlatformRoute,
})

function PlatformRoute() {
  if (isContainerRuntime()) return <ContainerPlatformNotice />
  return <PlatformPage />
}

// The demo starts its own Postgres, Readyset and proxy containers, which a
// server running in a container can't do.
function ContainerPlatformNotice() {
  return (
    <div className="mx-auto max-w-xl p-8">
      <Text as="h1" level="label-large" className="mb-2">
        The Readyset Platform demo runs in the desktop app
      </Text>
      <Text as="p" level="body-small" className="text-content-layout-3">
        It starts its own database and cache containers, which this server can't
        do from inside a container. Try it in the desktop app from{' '}
        <a
          href="https://readyset.io/downloads"
          target="_blank"
          rel="noreferrer"
          className="text-content-primary-soft underline-offset-4 hover:underline"
        >
          readyset.io/downloads
        </a>
        .
      </Text>
    </div>
  )
}
