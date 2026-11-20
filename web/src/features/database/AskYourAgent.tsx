// Connects the user's own coding agent to RDST, a local connection to this app, so it can pull
// what RDST sees: time series, Actions and index plans. Connect opens the Claude or Codex app
// on the conversation that set the database up (or a new one whose prompt carries the
// connection), adds RDST to the agent's CLI when there's no app, or copies the prompt. What's
// installed is looked up on that click, so the database page never waits on it. A quiet link
// then adds RDST to every Claude Code conversation. The agent changes the database only when
// the user asks; RDST itself doesn't take actions. A server-mode RDST is on another machine,
// so its card makes the server's agent key and copies commands that carry it instead.
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { AgentLogo } from '@/components/providers/ProviderLogos'
import {
  AGENT_KEY_PLACEHOLDER,
  type AgentKeyStatus,
  askInApp,
  connectAgent,
  createAgentKey,
  type DiscoveryAgent,
  fetchAgentKey,
  fetchAskPrompt,
  fetchDiscoveryOptions,
  revokeAgentKey,
} from '@/lib/agentDiscovery'
import { trackEvent } from '@/lib/analytics'
import { isContainerRuntime } from '@/lib/desktop'

const APP_NAMES = { claude: 'Claude', codex: 'Codex' } as const

type Options = { agents: DiscoveryAgent[]; apps: ('claude' | 'codex')[] }

/** The commands that add a server-mode RDST to an agent on the user's own machine: its MCP
 * endpoint at the address the browser uses, with the agent key. Codex reads a bearer token
 * only from an environment variable. */
export function serverAgentCommands(origin: string, key: string) {
  const url = `${origin}/mcp`
  return {
    claude: `claude mcp add --transport http rdst ${url} --header "Authorization: Bearer ${key}"`,
    codex: `export RDST_AGENT_KEY=${key}\ncodex mcp add rdst --url ${url} --bearer-token-env-var RDST_AGENT_KEY`,
  }
}

function when(seconds: number | null): string {
  if (!seconds) return 'never'
  return new Date(seconds * 1000).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

/** A server-mode RDST isn't on the user's computer: their agent connects over HTTPS with the
 * server's one agent key, so the card makes the key and hands over commands that carry it. */
function ServerAskYourAgent({ target }: { target: string }) {
  const [status, setStatus] = useState<AgentKeyStatus | null>(null)
  const [key, setKey] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    fetchAgentKey()
      .then((loaded) => {
        if (live) setStatus(loaded)
      })
      .catch(() => {
        if (live)
          setStatus({ exists: false, created_at: null, last_used_at: null })
      })
    return () => {
      live = false
    }
  }, [])

  const act = async (name: string, action: () => Promise<void>) => {
    setError(null)
    setBusy(name)
    try {
      await action()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }
  const create = () =>
    act('create', async () => {
      const made = await createAgentKey()
      setKey(made.key)
      setStatus({
        exists: true,
        created_at: made.created_at,
        last_used_at: made.last_used_at,
      })
      setDone(null)
    })
  const revoke = () =>
    act('revoke', async () => {
      setStatus(await revokeAgentKey())
      setKey(null)
      setDone('Key revoked. Agents that used it no longer have access.')
    })
  const copy = (name: string, text: () => Promise<string>, said: string) =>
    act(name, async () => {
      try {
        await navigator.clipboard.writeText(await text())
      } catch {
        throw new Error(
          "This browser won't copy from this address. Select the command below and copy it yourself."
        )
      }
      trackEvent('agent_ask', { action: 'copy' })
      setDone(said)
    })

  const commands = key ? serverAgentCommands(window.location.origin, key) : null

  return (
    <section
      aria-label="Connect Claude or Codex to RDST"
      data-testid="ask-your-agent"
      className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-3.5"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-control bg-surface-layout-2">
        <AgentLogo agent="claude" size={20} />
      </span>
      <span className="min-w-0 flex-1">
        <Text
          as="span"
          level="label-small"
          className="block text-content-layout-1"
        >
          Connect Claude or Codex to RDST
        </Text>
        <Text as="span" level="caption" className="block text-content-layout-3">
          {done ??
            `Let your agent pull what RDST sees on ${target}: the time series, Actions and index plans. It connects to this server with an agent key, and changes nothing unless you ask.`}
        </Text>
      </span>
      {commands && key ? (
        <span className="flex flex-wrap items-center gap-1">
          {(['claude', 'codex'] as const).map((which) => (
            <Button
              key={which}
              variant="primary"
              modifier="outline"
              size="small"
              label={`Copy ${APP_NAMES[which]} command`}
              loading={busy === which}
              disabled={busy !== null}
              onClick={() =>
                void copy(
                  which,
                  async () => commands[which],
                  `Copied. Run it in a terminal on your computer, then ask ${APP_NAMES[which]} about ${target}.`
                )
              }
              data-testid={
                which === 'claude' ? 'ask-connect' : 'ask-connect-codex'
              }
            >
              <AgentLogo agent={which} size={14} className="mr-1.5" />
            </Button>
          ))}
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Copy a prompt"
            loading={busy === 'copy'}
            disabled={busy !== null}
            onClick={() =>
              void copy(
                'copy',
                async () =>
                  (await fetchAskPrompt(target)).prompt.replaceAll(
                    AGENT_KEY_PLACEHOLDER,
                    key
                  ),
                'Prompt copied. Paste it into any coding agent.'
              )
            }
            data-testid="ask-copy"
          />
        </span>
      ) : (
        <span className="flex flex-wrap items-center gap-1">
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label={status?.exists ? 'Replace key' : 'Create connect command'}
            loading={busy === 'create'}
            disabled={busy !== null || status === null}
            onClick={() => void create()}
            data-testid="ask-create-key"
          />
          {status?.exists && (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Revoke"
              loading={busy === 'revoke'}
              disabled={busy !== null}
              onClick={() => void revoke()}
              data-testid="ask-revoke-key"
            />
          )}
        </span>
      )}
      {commands && (
        <pre
          data-testid="ask-command"
          className="w-full select-all whitespace-pre-wrap break-all rounded-control bg-surface-layout-2 px-3 py-2 font-mono text-caption text-content-layout-1"
        >
          {`# Claude Code\n${commands.claude}\n\n# Codex\n${commands.codex}`}
        </pre>
      )}
      <Text
        as="p"
        level="caption"
        className="w-full text-content-layout-3"
        data-testid="ask-key-note"
      >
        {key
          ? 'Shown once. Creating a new command replaces this key, and agents using the old one lose access.'
          : status?.exists
            ? `Agent key created ${when(status.created_at)}, last used ${when(status.last_used_at)}. Replacing it shows a new command and shuts out agents using this one.`
            : 'One agent key works for this server at a time. It opens only the agent tools, never this page.'}
      </Text>
      {error && (
        <div className="w-full">
          <Alert variant="negative" modifier="ghost" label={error} />
        </div>
      )}
    </section>
  )
}

export function AskYourAgent({ target }: { target: string }) {
  if (isContainerRuntime()) return <ServerAskYourAgent target={target} />
  return <LocalAskYourAgent target={target} />
}

function LocalAskYourAgent({ target }: { target: string }) {
  const [options, setOptions] = useState<Options | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async (): Promise<Options> => {
    if (options) return options
    const { agents, apps } = await fetchDiscoveryOptions()
    const found = {
      agents: agents.filter((item) => item.side === 'local'),
      apps,
    }
    setOptions(found)
    return found
  }

  const run = async (key: string, action: () => Promise<string>) => {
    setError(null)
    setBusy(key)
    try {
      setDone(await action())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const copyPrompt = async () => {
    const { prompt } = await fetchAskPrompt(target)
    await navigator.clipboard.writeText(prompt)
    trackEvent('agent_ask', { action: 'copy' })
  }

  // Its app when it's installed, else its CLI, else the prompt to paste.
  const connect = async (which: 'claude' | 'codex') => {
    const { agents, apps } = await load()
    if (apps.includes(which)) {
      const { resumed } = await askInApp(which, target)
      trackEvent('agent_ask', { action: 'open_app', app: which, resumed })
      return resumed
        ? `Opened the ${APP_NAMES[which]} conversation that set up ${target}.`
        : `Opened a new ${APP_NAMES[which]} conversation about ${target}. Send it to start.`
    }
    const agent = agents.find((item) => item.id === which)
    if (agent) {
      await connectAgent(agent.key)
      trackEvent('agent_ask', { action: 'connect', agent: agent.key })
      return `${agent.label} is connected to RDST. Ask about ${target} in any conversation.`
    }
    await copyPrompt()
    return `RDST didn't find ${APP_NAMES[which]} here, so it copied the prompt. Paste it into your coding agent.`
  }

  const agent = options?.agents[0]
  return (
    <section
      aria-label="Connect Claude or Codex to RDST"
      data-testid="ask-your-agent"
      className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-3.5"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-control bg-surface-layout-2">
        <AgentLogo agent="claude" size={20} />
      </span>
      <span className="min-w-0 flex-1">
        <Text
          as="span"
          level="label-small"
          className="block text-content-layout-1"
        >
          Connect Claude or Codex to RDST
        </Text>
        <Text as="span" level="caption" className="block text-content-layout-3">
          {done ??
            `Let your agent pull what RDST sees on ${target}: the time series, Actions and index plans. It's a local connection to this app, and it changes nothing unless you ask.`}
        </Text>
      </span>
      <span className="flex flex-wrap items-center gap-1">
        {(['claude', 'codex'] as const).map((which) => (
          <Button
            key={which}
            variant="primary"
            modifier="outline"
            size="small"
            label={`Connect ${APP_NAMES[which]}`}
            icon="arrow-up-right"
            iconPosition="right"
            loading={busy === which}
            disabled={busy !== null}
            onClick={() => void run(which, () => connect(which))}
            data-testid={
              which === 'claude' ? 'ask-connect' : 'ask-connect-codex'
            }
          >
            <AgentLogo agent={which} size={14} className="mr-1.5" />
          </Button>
        ))}
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Copy a prompt"
          loading={busy === 'copy'}
          disabled={busy !== null}
          onClick={() =>
            void run('copy', async () => {
              await copyPrompt()
              return 'Prompt copied. Paste it into any coding agent.'
            })
          }
          data-testid="ask-copy"
        />
      </span>
      {agent && (options?.apps.length ?? 0) > 0 && (
        <button
          type="button"
          className="w-full text-left text-caption text-content-layout-3 underline-offset-2 hover:text-content-layout-1 hover:underline disabled:opacity-50"
          disabled={busy !== null}
          onClick={() =>
            void run('everywhere', async () => {
              await connectAgent(agent.key)
              trackEvent('agent_ask', { action: 'connect', agent: agent.key })
              return `${agent.label} is connected to RDST. Ask about ${target} in any conversation.`
            })
          }
          data-testid="ask-everywhere"
        >
          {busy === 'everywhere'
            ? `Adding RDST to ${agent.label}`
            : `Or add RDST to every ${agent.label} conversation`}
        </button>
      )}
      {error && (
        <div className="w-full">
          <Alert variant="negative" modifier="ghost" label={error} />
        </div>
      )}
    </section>
  )
}
