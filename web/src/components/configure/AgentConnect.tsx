// The add-database dialog's agent path. When Claude Code or Codex is installed, RDST runs it to
// find the databases in the user's recent projects and lists them to pick from. Any agent can
// also take a copied prompt instead, and RDST waits while it connects and adds the database.
// Once one is added, the database's page opens on the same turn-on dialog the connection form
// leads to, with its prerequisite checks, and follows the agent from there.
import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Spinner } from '@rs/ui-new/spinner'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import { PRIVACY_DOC_URL } from '../../features/onboarding/onboardingState'
import {
  type AddAttempt,
  abandonAttempt,
  handOffAddFlow,
  newAddAttempt,
  onceForAttempt,
} from '../../lib/addFlow'
import {
  type AgentProgress,
  type AgentPrompt,
  advanceAgentProgress,
  fetchAgentPrompt,
  NO_PROGRESS,
  subscribeAgentConnect,
} from '../../lib/agentConnect'
import {
  type FoundDatabase,
  openInApp,
  PROMPT_SUMMARY,
  type SignInProvider,
} from '../../lib/agentDiscovery'
import {
  agentAttemptId,
  type DatabaseAddStage,
  trackEvent,
} from '../../lib/analytics'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { userErrorClass } from '../../lib/userErrorClass'
import { notifyDatabasesChanged } from './addDatabase'
import { FindDatabases } from './FindDatabases'

/** How long "Added" stays up before the database's page opens on its turn-on dialog. */
const HANDOFF_DELAY_MS = 1400

/** One small link to the write-up on what RDST stores and where. */
export function DataLine() {
  return (
    <Text as="p" level="caption" className="text-content-layout-3">
      <a
        href={PRIVACY_DOC_URL}
        target="_blank"
        rel="noreferrer"
        className="underline decoration-border-layout-2 underline-offset-2 transition-colors duration-fast hover:text-content-layout-1"
      >
        How your data is handled
      </a>
    </Text>
  )
}

/** The prompt as a card: a few lines with a fade, the rest a click away, and Copy. */
function PromptCard({
  prompt,
  caption,
  copied,
  primary,
  onCopy,
}: {
  prompt: AgentPrompt | null
  caption?: string
  copied: boolean
  /** Copy is the view's one solid action until the agent connects. */
  primary: boolean
  onCopy: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const body = (
    <Text
      as="p"
      level="body-small"
      className="whitespace-pre-wrap break-words text-content-layout-2"
      data-testid="agent-prompt"
    >
      {prompt?.prompt}
    </Text>
  )
  return (
    <section
      aria-label="Prompt for your agent"
      data-testid="agent-prompt-card"
      className="ph-no-capture overflow-hidden rounded-card border border-border-layout-2 bg-surface-layout-1"
    >
      <div className="flex items-center gap-3 px-4 pt-3.5 pb-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-control bg-surface-rising-soft text-content-rising-soft">
          <Icon name="sparkles" label="" size="small" />
        </span>
        <span className="min-w-0 flex-1">
          <Text
            as="span"
            level="label-medium"
            className="block text-content-layout-1"
          >
            Prompt for your agent
          </Text>
          {caption && (
            <Text
              as="span"
              level="caption"
              className="block text-content-layout-3"
              data-testid="agent-prompt-caption"
            >
              {caption}
            </Text>
          )}
        </span>
        <Button
          variant="primary"
          modifier={primary ? 'solid' : 'outline'}
          size="small"
          icon={copied ? 'tick' : 'copy'}
          iconPosition="left"
          label={copied ? 'Copied' : 'Copy'}
          disabled={!prompt}
          onClick={onCopy}
          data-testid="agent-copy-prompt"
        />
      </div>
      <div className="border-t border-border-layout-2">
        {prompt ? (
          <div className="relative">
            {expanded ? (
              <Scrollable rootClassName="max-h-60" className="px-4 py-3">
                {body}
              </Scrollable>
            ) : (
              <div className="max-h-18 overflow-hidden px-4 pt-3">{body}</div>
            )}
            {!expanded && (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-0 bottom-0 h-9 bg-linear-to-t from-surface-layout-1 to-transparent"
              />
            )}
          </div>
        ) : (
          <div
            className="flex flex-col gap-2 px-4 pt-3.5 pb-1"
            aria-busy="true"
          >
            <Skeleton className="h-3 w-11/12" />
            <Skeleton className="h-3 w-10/12" />
            <Skeleton className="h-3 w-7/12" />
          </div>
        )}
        <div className="px-1 pt-0.5 pb-1.5">
          <Button
            variant="primary"
            modifier="link"
            size="small"
            icon={expanded ? 'chevron-up' : 'chevron-down'}
            iconPosition="right"
            label={expanded ? 'Hide full prompt' : 'Show full prompt'}
            disabled={!prompt}
            onClick={() => setExpanded((open) => !open)}
            aria-expanded={expanded}
            className="no-underline"
          />
        </div>
      </div>
    </section>
  )
}

type AgentState = 'waiting' | 'connected' | 'added' | 'error'

/** Where the agent is, in one live row: waiting, connected, then the database added. */
function AgentStatus({
  state,
  title,
  caption,
}: {
  state: AgentState
  title: string
  caption: ReactNode
}) {
  const reduced = useReducedMotion()
  return (
    <div
      data-testid="agent-status"
      data-state={state}
      aria-live="polite"
      className="flex items-center gap-3 rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-3"
    >
      <span className="relative grid size-7 shrink-0 place-items-center">
        <AnimatePresence initial={false} mode="popLayout">
          <m.span
            key={state}
            initial={reduced ? { opacity: 0 } : { scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 520, damping: 26 }}
            className={cn(
              'grid size-7 place-items-center rounded-pill',
              state === 'added'
                ? 'bg-surface-positive-soft text-content-positive-soft'
                : state === 'error'
                  ? 'bg-surface-negative-soft text-content-negative-soft'
                  : 'border border-border-layout-2 bg-surface-layout-2'
            )}
          >
            {state === 'added' ? (
              <Icon name="tick" label="Done" size="small" />
            ) : state === 'error' ? (
              <Icon name="alert" label="Problem" size="small" />
            ) : (
              <Spinner size="base" color="primary-soft" />
            )}
          </m.span>
        </AnimatePresence>
      </span>
      <span className="min-w-0 flex-1">
        <Text
          as="span"
          level="body-small"
          className="block text-content-layout-1"
        >
          {title}
        </Text>
        <Text as="span" level="caption" className="block text-content-layout-3">
          {caption}
        </Text>
      </span>
    </div>
  )
}

export function AgentConnect({
  onAddByHand,
  onSignIn,
  onFillIn,
  attempt: given,
}: {
  /** Leave the agent path for the connection form. */
  onAddByHand: () => void
  /** A found database lives with a provider: open its sign-in and import. */
  onSignIn?: (provider: SignInProvider) => void
  /** Open the connection form filled in with a found database. */
  onFillIn?: (found: FoundDatabase) => void
  /** The dialog's add attempt, for the funnel it reports. */
  attempt?: AddAttempt
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [own] = useState(newAddAttempt)
  const attempt = given ?? own
  const [prompt, setPrompt] = useState<AgentPrompt | null>(null)
  const [promptError, setPromptError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [settingUp, setSettingUp] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)
  const [progress, setProgress] = useState<AgentProgress>(NO_PROGRESS)
  const [opening, setOpening] = useState(false)
  const loadRef = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    loadRef.current?.abort()
    const controller = new AbortController()
    loadRef.current = controller
    setPromptError(null)
    setCopied(false)
    try {
      const next = await fetchAgentPrompt(controller.signal)
      if (!controller.signal.aborted) setPrompt(next)
    } catch (caught) {
      if (controller.signal.aborted) return
      setPromptError(
        caught instanceof Error
          ? caught.message
          : "RDST couldn't make a prompt."
      )
    }
  }, [])

  useEffect(() => {
    void load()
    return () => loadRef.current?.abort()
  }, [load])

  // Each prompt is one attempt, named by a hash of its nonce.
  const nonce = prompt?.attempt ?? null
  const issuedAt = prompt?.issuedAt ?? null
  useEffect(() => {
    if (!prompt) return
    let live = true
    void agentAttemptId(nonce).then((id) => {
      if (!live) return
      onceForAttempt(attempt, `prompt-${id ?? issuedAt}`, () =>
        trackEvent('agent_prompt_shown', { attempt_id: id })
      )
    })
    return () => {
      live = false
    }
  }, [prompt, nonce, issuedAt, attempt])

  // Each prompt follows its own progress, replayed from when it was made, so steps that
  // land before the stream opens still count.
  const promptLoaded = prompt !== null
  useEffect(() => {
    if (!promptLoaded) return
    setProgress(NO_PROGRESS)
    // An agent RDST runs itself reports in FindDatabases, not here.
    return subscribeAgentConnect(
      (event) => {
        if (!event.run)
          setProgress((current) => advanceAgentProgress(current, event))
      },
      { since: issuedAt }
    )
  }, [promptLoaded, issuedAt])

  const connected = progress.done.has('connected')
  const added = progress.done.has('database_added')
  const monitored = progress.done.has('monitoring_enabled')
  const target = progress.target

  // Where the attempt stands, for the funnel if the dialog closes here.
  const stage: DatabaseAddStage = added
    ? 'database_added'
    : progress.error
      ? 'agent_error'
      : connected
        ? 'paired'
        : prompt
          ? 'prompt_shown'
          : promptError
            ? 'prompt_error'
            : 'prompt_loading'
  const stageRef = useRef(stage)
  stageRef.current = stage
  const handedOff = useRef(false)
  useEffect(
    () => () => {
      if (handedOff.current || attempt.switching) return
      abandonAttempt(attempt, 'agent', stageRef.current)
    },
    [attempt]
  )

  // The agent's test verdicts, in the same event the connection form sends.
  useEffect(() => {
    if (progress.verdicts > 0)
      onceForAttempt(attempt, 'test-true', () =>
        trackEvent('database_add_test_result', {
          ok: true,
          error_class: null,
          via: 'agent',
        })
      )
  }, [progress.verdicts, attempt])
  useEffect(() => {
    if (progress.error && added)
      onceForAttempt(attempt, 'test-false', () =>
        trackEvent('database_add_test_result', {
          ok: false,
          error_class: userErrorClass(progress.error),
          via: 'agent',
        })
      )
  }, [progress.error, added, attempt])

  const openDatabase = useCallback(
    async (name: string, enable: boolean) => {
      setOpening(true)
      handedOff.current = true
      handOffAddFlow(name, { attempt, via: 'agent', agentSince: issuedAt })
      selectGlobalTarget(name)
      startBootstrapRun(name)
      await queryClient.refetchQueries({ queryKey: ['status'] })
      notifyDatabasesChanged({ recheck: [name] })
      void navigate({
        to: '/database',
        search: enable ? { target: name, panel: 'enable' } : { target: name },
      })
    },
    [navigate, queryClient, attempt, issuedAt]
  )

  // Added: the database's page takes over, on the turn-on dialog unless the agent already
  // turned monitoring on. An agent RDST is running to set a database up stays in view
  // instead, until it needs the user.
  useEffect(() => {
    if (!added || !target || settingUp) return
    const timer = window.setTimeout(
      () => void openDatabase(target, !monitored),
      HANDOFF_DELAY_MS
    )
    return () => window.clearTimeout(timer)
  }, [added, target, monitored, openDatabase, settingUp])

  const copy = async () => {
    if (!prompt) return
    try {
      await navigator.clipboard.writeText(prompt.prompt)
      setCopied(true)
      void agentAttemptId(prompt.attempt).then((id) =>
        trackEvent('agent_prompt_copied', { attempt_id: id })
      )
    } catch {
      setPromptError("RDST couldn't copy the prompt. Select it and copy it.")
    }
  }

  const manually = () => {
    trackEvent('find_option_chosen', { option: 'manual' })
    attempt.switching = true
    onAddByHand()
  }

  // No prompt to show: say so, offer another try, and make the form the obvious way on.
  if (!prompt && promptError)
    return (
      <>
        <Scrollable rootClassName="min-h-0" className="px-6 pt-2 pb-5">
          <div
            className="rounded-card border border-border-layout-2 bg-surface-layout-1 px-4 py-4"
            data-testid="agent-prompt-error"
          >
            <Alert
              variant="negative"
              modifier="ghost"
              label="Couldn't get a prompt from RDST"
            />
            <Text as="p" level="caption" className="mt-2 text-content-layout-3">
              {promptError} Try again, or connect with a connection string.
            </Text>
            <div className="mt-3">
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Try again"
                onClick={() => void load()}
              />
            </div>
          </div>
        </Scrollable>
        <div className="flex items-center justify-end gap-2 border-t border-border-layout-2 px-6 py-3.5">
          <Button
            variant="primary"
            modifier="solid"
            label="Connect with a URL instead"
            onClick={manually}
            data-testid="agent-connect-url-instead"
          />
        </div>
      </>
    )

  const caption = connected ? 'Your agent connected.' : undefined

  const status: { state: AgentState; title: string; caption: ReactNode } =
    added && target
      ? {
          state: 'added',
          title: progress.awaitingUser
            ? 'Your turn: read and click Turn on monitoring'
            : `Added ${target}`,
          caption: 'Opening monitoring setup.',
        }
      : progress.error
        ? {
            state: 'error',
            title: 'Your agent hit a problem',
            caption: progress.error,
          }
        : connected
          ? {
              state: 'connected',
              title: 'Your agent connected',
              caption: "It's finding the database your project uses.",
            }
          : {
              state: 'waiting',
              title: 'Waiting for your agent',
              caption: copied
                ? 'Paste the prompt into your agent.'
                : 'Copy the prompt, then paste it into your agent.',
            }

  return (
    <>
      <Scrollable rootClassName="min-h-0" className="px-6 pt-2 pb-5">
        <div className="mb-5">
          <FindDatabases
            onOpen={(name, enable) => {
              attempt.switching = true
              void openDatabase(name, enable)
            }}
            onSetupChange={setSettingUp}
            onPastePrompt={() => setShowPrompt(true)}
            promptShown={showPrompt}
            onOpenApp={async (app) => {
              if (!prompt?.attempt)
                throw new Error('RDST is still making the prompt.')
              await openInApp(app, prompt.attempt)
              setShowPrompt(true)
            }}
            onSignIn={(provider) => {
              attempt.switching = true
              onSignIn?.(provider)
            }}
            onFillIn={(found) => {
              attempt.switching = true
              if (found) onFillIn?.(found)
              else onAddByHand()
            }}
            onEdit={(name) => {
              attempt.switching = true
              void navigate({
                to: '/configure',
                search: {
                  edit: name,
                  returnTo: `${window.location.pathname}${window.location.search}`,
                },
              })
            }}
          />
        </div>

        {showPrompt && (
          <>
            <Text
              as="p"
              level="body-small"
              className="text-content-layout-2"
              data-testid="agent-prompt-summary"
            >
              {PROMPT_SUMMARY}
            </Text>

            <div className="mt-4">
              <PromptCard
                prompt={prompt}
                caption={caption}
                copied={copied}
                primary={!connected}
                onCopy={() => void copy()}
              />
            </div>
          </>
        )}

        {promptError && (
          <div className="mt-3">
            <Alert variant="negative" modifier="ghost" label={promptError} />
          </div>
        )}

        {(showPrompt || copied || connected || added || progress.error) && (
          <div className="mt-3">
            <AgentStatus {...status} />
          </div>
        )}

        {progress.error && (
          <Text as="p" level="caption" className="mt-2 text-content-layout-3">
            Ask your agent to try again
            {target ? (
              <>
                , or{' '}
                <Pressable
                  className="cursor-pointer underline decoration-border-layout-2 underline-offset-2 hover:text-content-layout-1"
                  onClick={() => void openDatabase(target, true)}
                >
                  finish on {target}'s page
                </Pressable>
                .
              </>
            ) : (
              ', or connect manually.'
            )}
          </Text>
        )}

        <div className="mt-5">
          <DataLine />
        </div>
      </Scrollable>
      <div className="flex items-center gap-2 border-t border-border-layout-2 px-6 py-3.5">
        {!added && (
          <Button
            variant="primary"
            modifier="ghost"
            label="Connect manually instead"
            onClick={manually}
            data-testid="agent-connect-manually"
          />
        )}
        <span className="flex-1" />
        {added && target && (
          <Button
            variant="primary"
            modifier="solid"
            label="Continue"
            loading={opening}
            onClick={() => void openDatabase(target, !monitored)}
          />
        )}
      </div>
    </>
  )
}

/** The agent path's entry on the add-database form, above the providers. */
export function AgentConnectOption({ onPick }: { onPick: () => void }) {
  return (
    <Pressable
      onClick={onPick}
      data-testid="agent-connect-option"
      className="group grid w-full cursor-pointer grid-cols-[2.25rem_1fr_auto] items-center gap-3 rounded-panel border border-border-layout-2 bg-surface-layout-2 px-3.5 py-3 text-left transition-colors duration-fast ease-base hover:border-border-primary-soft hover:bg-surface-primary-soft-hover"
    >
      <span className="grid size-9 place-items-center rounded-control bg-surface-rising-soft text-content-rising-soft">
        <Icon name="search" label="" size="small" />
      </span>
      <span className="min-w-0">
        <Text
          as="span"
          level="body-small"
          className="block text-content-layout-1"
        >
          Import local databases
        </Text>
        <Text as="span" level="caption" className="block text-content-layout-3">
          From your recent projects, Docker and the databases running on this
          computer.
        </Text>
      </span>
      <Icon
        name="chevron-right"
        label=""
        size="small"
        className="text-content-layout-3 transition-transform duration-fast ease-base group-hover:translate-x-0.5"
      />
    </Pressable>
  )
}
