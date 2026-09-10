import { createFileRoute, redirect } from '@tanstack/react-router'

// The Agents workspace was retired from the app: agents are created and run
// from the CLI (`rdst agent`), the MCP server and the Slack bot. The URL stays
// as a redirect so bookmarks and doc links keep working, and it carries
// `from=agents` so Ask can say what happened instead of swapping the page out
// from under the reader. [F-01, F-02]
export const Route = createFileRoute('/agents')({
  beforeLoad: () => {
    throw redirect({ to: '/ask', search: { from: 'agents' } })
  },
})
