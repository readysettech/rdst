import { createFileRoute } from '@tanstack/react-router'
import { PlatformPage } from '../features/platform/PlatformPage'

export const Route = createFileRoute('/platform')({
  staticData: { layout: 'page' },
  component: PlatformPage,
})
