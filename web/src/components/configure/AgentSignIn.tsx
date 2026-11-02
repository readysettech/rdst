/**
 * Signing a coding agent in when RDST finds it signed out on this computer: a terminal opens
 * on the agent's own sign-in, and RDST watches for it to finish before trying again.
 */
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'

import {
  type DiscoveryAgent,
  fetchDiscoveryOptions,
  signInAgent,
} from '../../lib/agentDiscovery'
import { trackEvent } from '../../lib/analytics'

const POLL_MS = 4000
// Long enough for a browser sign-in; after that the user tries again by hand.
const WATCH_MS = 5 * 60 * 1000

export function AgentSignIn({
  agent,
  onSignedIn,
  retryLabel = 'Try again',
  inApp,
}: {
  agent: DiscoveryAgent
  /** It's signed in now: run again with it. */
  onSignedIn: () => void
  retryLabel?: string
  /** The agent's desktop app, signed in on its own: a new conversation there, with the
   * prompt typed in, which RDST follows once it connects. */
  inApp?: { name: string; open: () => Promise<void> }
}) {
  const [opened, setOpened] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    if (opened === null) return
    let live = true
    const timer = window.setInterval(() => {
      if (Date.now() - opened > WATCH_MS) {
        window.clearInterval(timer)
        return
      }
      fetchDiscoveryOptions(true)
        .then(({ agents }) => {
          const now = agents.find((item) => item.id === agent.id)
          if (!live || !now?.signed_in) return
          window.clearInterval(timer)
          trackEvent('find_agent_signed_in', { agent: now.key })
          onSignedIn()
        })
        .catch(() => {})
    }, POLL_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [opened, agent.id, onSignedIn])

  return (
    <div className="space-y-2" data-testid="agent-sign-in">
      <Text as="p" level="body-small" className="text-content-layout-2">
        {opened === null
          ? `${agent.via === 'app' ? `RDST runs the ${agent.label} that came with your ${agent.name} app, and it isn't signed in yet. Sign in with the same account` : `${agent.label} is signed out on this computer. Sign in to it`} and RDST picks up where it left off${inApp ? `, or carry on in the ${inApp.name} app` : ''}.`
          : `Finish signing in to ${agent.label} in the window that opened. RDST carries on once you're signed in.`}
      </Text>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          modifier={opened === null ? 'solid' : 'outline'}
          label={
            opened === null ? `Sign in to ${agent.label}` : 'Open it again'
          }
          onClick={() => {
            setError(null)
            trackEvent('find_agent_sign_in', { agent: agent.key })
            signInAgent(agent.key)
              .then(() => setOpened(Date.now()))
              .catch((caught) =>
                setError(
                  caught instanceof Error ? caught.message : String(caught)
                )
              )
          }}
          data-testid="agent-sign-in-open"
        />
        {inApp && (
          <Button
            variant="primary"
            modifier="ghost"
            label={`Continue in the ${inApp.name} app`}
            icon="arrow-up-right"
            iconPosition="right"
            loading={opening}
            onClick={() => {
              setError(null)
              setOpening(true)
              trackEvent('find_agent_sign_in', {
                agent: agent.key,
                via: 'app',
              })
              inApp
                .open()
                .catch((caught) =>
                  setError(
                    caught instanceof Error ? caught.message : String(caught)
                  )
                )
                .finally(() => setOpening(false))
            }}
            data-testid="agent-sign-in-app"
          />
        )}
        {opened !== null && (
          <Button
            variant="primary"
            modifier="ghost"
            label={retryLabel}
            onClick={onSignedIn}
            data-testid="agent-sign-in-retry"
          />
        )}
      </div>
      {error && (
        <Text as="p" level="caption" className="text-content-negative-soft">
          {error}
        </Text>
      )}
    </div>
  )
}
