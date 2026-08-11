import { createFileRoute } from '@tanstack/react-router'
import { AskPage } from './-ask-page'

export const Route = createFileRoute('/ask')({
  component: AskPage,
})
