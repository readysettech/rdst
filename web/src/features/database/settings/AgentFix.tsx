// A database that isn't ready to monitor can be handed to the user's coding agent: RDST runs
// it in the background (Fix with Claude), stages the fix prompt in the Claude or Codex app,
// or gives the prompt to copy. The agent fixes what it safely can and asks before starting,
// restarting or changing anything; the dialog checks again when it's finished.
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useEffect, useRef, useState } from 'react'
import { QuestionCard } from '@/components/configure/FindDatabases'
import { AgentLogo } from '@/components/providers/ProviderLogos'
import {
  askInApp,
  cancelDiscovery,
  type DiscoveryAgent,
  type DiscoveryRun,
  fetchConversation,
  fetchDiscovery,
  fetchDiscoveryOptions,
  fetchFixPrompt,
  openInApp,
  startAgentFix,
} from '@/lib/agentDiscovery'
import { trackEvent } from '@/lib/analytics'

const POLL_MS = 1200
const APP_LABELS = { claude: 'Claude', codex: 'Codex' } as const

export function AgentFix({
  target,
  onFinished,
  problem,
}: {
  target: string
  /** The agent stopped: check the prerequisites again. */
  onFinished: () => void
  /** What's wrong, for an agent that carries on in its own app. */
  problem?: string | null
}) {
  const [agent, setAgent] = useState<DiscoveryAgent | null>(null)
  const [apps, setApps] = useState<('claude' | 'codex')[]>([])
  const [loaded, setLoaded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [run, setRun] = useState<DiscoveryRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  // The conversation the user took to the Claude or Codex app: fixing carries on there.
  const [inApp, setInApp] = useState<'claude' | 'codex' | null>(null)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    let live = true
    Promise.all([
      fetchDiscoveryOptions(),
      fetchConversation(target).catch(() => ({ in_app: null })),
    ])
      .then(([{ agents, apps: installed }, conversation]) => {
        if (!live) return
        setAgent(agents[0] ?? null)
        setApps(installed)
        const app = conversation.in_app
        if ((app === 'claude' || app === 'codex') && installed.includes(app))
          setInApp(app)
      })
      .catch(() => {})
      .finally(() => live && setLoaded(true))
    return () => {
      live = false
    }
  }, [target])

  const runId = run?.state === 'running' ? run.id : null
  useEffect(() => {
    if (!runId) return
    const timer = window.setInterval(() => {
      fetchDiscovery(runId)
        .then((next) => {
          setRun(next)
          if (next.state !== 'running') {
            trackEvent('agent_help', {
              stage: 'finished',
              where: 'turn_on',
              run_id: next.id,
              agent: next.agent?.key ?? 'unknown',
              state: next.state,
              seconds: next.elapsed,
              tool_steps: next.activity.filter((line) => line.kind === 'tool')
                .length,
            })
            finished.current()
          }
        })
        .catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [runId])

  if (!loaded) return null

  // Its conversation lives in the agent's app now: that's the one place to fix it.
  if (inApp) {
    const continueThere = async () => {
      setError(null)
      setBusy(inApp)
      try {
        if (problem)
          await navigator.clipboard
            .writeText(`RDST still can't connect to ${target}: ${problem}`)
            .catch(() => {})
        await askInApp(inApp, target)
        setCopied(Boolean(problem))
        trackEvent('agent_help', {
          stage: 'started',
          where: 'turn_on',
          run_id: target,
          agent: `${inApp}:app`,
        })
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setBusy(null)
      }
    }
    return (
      <section
        aria-label={`Continue in the ${APP_LABELS[inApp]} app`}
        data-testid="agent-fix-in-app"
        className="mb-4 flex flex-wrap items-center gap-3 rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-3.5"
      >
        <span className="min-w-0 flex-1">
          <Text
            as="span"
            level="label-small"
            className="block text-content-layout-1"
          >
            {`${target}'s conversation is in the ${APP_LABELS[inApp]} app`}
          </Text>
          <Text
            as="span"
            level="caption"
            className="block text-content-layout-3"
          >
            {copied
              ? 'The error is copied: paste it there and Claude picks up from it.'
              : 'Carry on there: it knows this database and has RDST connected.'}
          </Text>
        </span>
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          label={`Continue in the ${APP_LABELS[inApp]} app`}
          icon="arrow-up-right"
          iconPosition="right"
          loading={busy === inApp}
          onClick={() => void continueThere()}
          data-testid="agent-fix-continue-in-app"
        >
          <AgentLogo agent={inApp} size={14} className="mr-1.5" />
        </Button>
        {error && (
          <div className="w-full">
            <Alert variant="negative" modifier="ghost" label={error} />
          </div>
        )}
      </section>
    )
  }
  const name = agent?.name ?? 'your coding agent'
  const appName = { claude: 'Claude', codex: 'Codex' }

  // The user's own agent, with this database's fix prompt: staged in its app, or copied.
  const handOff = async (to: 'claude' | 'codex' | 'copy') => {
    setError(null)
    setBusy(to)
    try {
      const { prompt, attempt } = await fetchFixPrompt(target)
      if (to === 'copy') {
        await navigator.clipboard.writeText(prompt)
        setCopied(true)
      } else {
        await openInApp(to, attempt, target)
      }
      trackEvent('agent_help', {
        stage: 'started',
        where: 'turn_on',
        run_id: attempt,
        agent: to === 'copy' ? 'prompt' : `${to}:app`,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  const start = async () => {
    if (!agent) return
    setError(null)
    setStarting(true)
    try {
      const started = await startAgentFix(agent.key, [target])
      setRun(started)
      trackEvent('agent_help', {
        stage: 'started',
        where: 'turn_on',
        run_id: started.id,
        agent: agent.key,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setStarting(false)
    }
  }

  const running = run?.state === 'running'
  return (
    <section
      aria-label={`Fix with ${name}`}
      data-testid="agent-fix"
      className="mb-4 rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-3.5"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-control bg-surface-rising-soft text-content-rising-soft">
          <Icon name="sparkles" label="" size="small" />
        </span>
        <span className="min-w-0 flex-1">
          <Text
            as="span"
            level="label-small"
            className="block text-content-layout-1"
          >
            {running
              ? `${name} is getting ${target} ready`
              : run
                ? (run.message?.split(/\n\s*\n/)[0] ?? `${name} stopped`)
                : agent
                  ? `Let ${name} fix this`
                  : 'Have your coding agent fix this'}
          </Text>
          {!run && (
            <Text
              as="span"
              level="caption"
              className="block text-content-layout-3"
            >
              It works out what's wrong and fixes what it safely can, and asks
              you before it starts, restarts or changes anything.
            </Text>
          )}
          {running && (
            <Text
              as="span"
              level="caption"
              className="block truncate text-content-layout-3"
              data-testid="agent-fix-now"
            >
              {[...run.activity].reverse().find((line) => line.kind !== 'tool')
                ?.text ?? `Starting ${agent?.label ?? name}`}
            </Text>
          )}
        </span>
        {running ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Stop"
            onClick={() =>
              void cancelDiscovery(run.id)
                .then(setRun)
                .catch(() => {})
            }
          />
        ) : (
          agent && (
            <Button
              variant="primary"
              modifier={run ? 'ghost' : 'outline'}
              size="small"
              label={run ? 'Try again' : `Fix with ${name}`}
              loading={starting}
              onClick={() => void start()}
              data-testid="agent-fix-start"
            >
              <AgentLogo agent={agent.id} className="mr-1.5" />
            </Button>
          )
        )}
      </div>
      {!running && (
        <div
          className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 pl-11"
          data-testid="agent-fix-other-ways"
        >
          <Text
            as="span"
            level="caption"
            className="mr-1 text-content-layout-3"
          >
            {agent ? 'Or with your own agent:' : 'With your own agent:'}
          </Text>
          {apps.map((app) => (
            <Button
              key={app}
              variant="primary"
              modifier="ghost"
              size="small"
              label={`Start in the ${appName[app]} app`}
              icon="arrow-up-right"
              iconPosition="right"
              loading={busy === app}
              disabled={busy !== null}
              onClick={() => void handOff(app)}
              data-testid={`agent-fix-in-${app}`}
            >
              <AgentLogo agent={app} size={14} className="mr-1.5" />
            </Button>
          ))}
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label={copied ? 'Prompt copied' : 'Copy a prompt'}
            loading={busy === 'copy'}
            disabled={busy !== null}
            onClick={() => void handOff('copy')}
            data-testid="agent-fix-copy"
          />
        </div>
      )}
      {run && (
        <div className="mt-3">
          {!running && run.question && (
            <div className="mt-2">
              <QuestionCard run={run} onAnswered={setRun} />
            </div>
          )}
        </div>
      )}
      {error && (
        <div className="mt-3">
          <Alert variant="negative" modifier="ghost" label={error} />
        </div>
      )}
    </section>
  )
}
