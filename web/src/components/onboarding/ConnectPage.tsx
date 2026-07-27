import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouter } from '@tanstack/react-router'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { useConfigure } from '../../lib/useConfigure'
import { useOnboarding } from '../../lib/useOnboarding'
import type { ConfigureFormData } from '../../types/configure'
import { ConfigureForm } from '../configure'

/**
 * First-run "Connect your database" — a single, exitable page that replaces the
 * four-step `fixed inset-0` wizard (onboarding-and-first-run). One job: point
 * RDST at a database. The AI key is deferred to just-in-time (never asked here),
 * the demo is offered as a zero-setup escape hatch, and the page is a normal
 * route inside the app shell — skippable, never a takeover.
 * [USE-006, USE-008, USE-068, USE-050/052, VIS-011, VIS-116]
 */
export function ConnectPage({
  redirectTo,
  from,
}: {
  redirectTo?: string
  from?: string
}) {
  const fromDemo = from === 'demo'
  const navigate = useNavigate()
  const router = useRouter()
  const queryClient = useQueryClient()
  const { addTarget, setDefaultTarget, loading } = useConfigure()
  const { completeInit } = useOnboarding()

  const leave = () => {
    // Return to where the user was headed when routed here, else Home.
    if (redirectTo && redirectTo !== '/onboarding') {
      router.history.push(redirectTo)
    } else {
      navigate({ to: '/' })
    }
  }

  const finishToHome = () => {
    queryClient.setQueryData(
      ['init-status'],
      (previous: { initialized?: boolean } | undefined) =>
        previous ? { ...previous, initialized: true } : previous
    )
    queryClient.invalidateQueries({ queryKey: ['init-status'] })
    queryClient.invalidateQueries({ queryKey: ['status'] })
    leave()
  }

  const handleSubmit = async (data: ConfigureFormData) => {
    try {
      await addTarget(data)
      await setDefaultTarget(data.name)
      await completeInit()
    } catch {
      // useConfigure surfaces the failure inline; stay on the page so the
      // user can fix the connection details instead of dead-ending.
      return
    }
    // Kick off the background bootstrap (schema, AI descriptions, optional
    // Readyset deploy); it never throws, and the sidebar chip tracks it
    // while the user lands in the app.
    startBootstrapRun(data.name, { deploy: data.deploy })
    toast({ title: `Connected to ${data.name}`, variant: 'positive' })
    finishToHome()
  }

  const skip = () => {
    // "I'll do it later" goes Home, which is always reachable. Returning to
    // the page that routed here would just bounce back when it still has no
    // target (e.g. a feature page needing a database).
    navigate({ to: '/' })
  }

  return (
    // Not a fixed takeover: a normal, exitable page inside the app shell. A
    // subtle accent-tinted hero surface (fading to the base plane) gives depth
    // and personality without a bespoke token, so the raised form card below
    // reads as elevated against it. [VIS-075, VIS-099, VIS-101, VIS-102]
    <div className="w-full bg-gradient-to-b from-surface-primary-soft/10 to-transparent">
      {/* Utility row: one canonical brand mark + exit */}
      <HStack className="justify-between items-center py-2">
        <HStack className="gap-2 items-center">
          <Icon
            name="querypilot"
            label="RDST"
            className="w-5 h-5 text-content-primary-soft"
          />
          <Text level="label-medium" className="text-content-layout-1">
            RDST
          </Text>
        </HStack>
        <button
          type="button"
          onClick={skip}
          className="text-content-layout-3 hover:text-content-layout-1 transition-colors text-sm inline-flex items-center gap-1"
        >
          Skip for now
          <Icon name="arrow-right" label="" className="w-3.5 h-3.5" />
        </button>
      </HStack>

      <div className="mx-auto w-full max-w-xl pb-16 pt-8">
        {fromDemo && (
          // One-line bridge from the demo→conviction hand-off: acknowledges the
          // watched win and carries the scent of information forward. The email
          // collected at the demo gate already persists, so nothing re-gates.
          // [demo-to-conviction step 7, USE-022, MET-008]
          <div className="mb-6 flex items-start gap-2 rounded-lg border border-border-primary-soft bg-surface-primary-soft/50 px-4 py-3">
            <Icon
              name="tick"
              label=""
              className="mt-0.5 h-4 w-4 shrink-0 text-content-primary-soft"
            />
            <Text level="body-small" className="text-content-layout-2">
              You&rsquo;ve seen the demo — now connect your own database to find
              your caching wins.
            </Text>
          </div>
        )}

        <VStack className="gap-2 items-start mb-6">
          <Text as="h1" level="headline-1" className="text-content-layout-1">
            Connect your database
          </Text>
          <Text
            level="body-medium"
            className="text-content-layout-2 leading-relaxed"
          >
            RDST — the Readyset Data &amp; SQL Toolkit. Point it at your
            Postgres or MySQL to find slow queries, health issues, and caching
            wins.
          </Text>
        </VStack>

        {/* AWS-first path: one click into the Settings discovery drawer for
            the common case of databases living in RDS/Aurora, ahead of the
            manual form. */}
        <div className="mb-6 flex items-center justify-between gap-3 rounded-[1.25rem] border border-border-layout-1 bg-surface-raised px-5 py-4 shadow-elevation-1">
          <VStack className="gap-0.5 items-start">
            <Text level="label-medium" className="text-content-layout-1">
              Databases on AWS?
            </Text>
            <Text level="caption" className="text-content-layout-3">
              Sign in with AWS and import your RDS/Aurora instances
              automatically.
            </Text>
          </VStack>
          <Button
            variant="primary"
            modifier="outline"
            label="Discover from AWS"
            icon="search"
            iconPosition="left"
            onClick={() => navigate({ to: '/configure', search: { add: 'aws' } })}
          />
        </div>

        {/* One raised card on the hero surface: the form, its inline test
            state, and its single primary CTA read as one grouped unit — no dead
            band, no floating second list card. [VIS-036, VIS-111, VIS-022] */}
        <div className="rounded-[1.25rem] shadow-elevation-1">
          <ConfigureForm
            onSubmit={handleSubmit}
            isLoading={loading}
            submitLabel="Test & connect"
            submitSize="large"
          />
        </div>

        <VStack className="gap-3 items-center mt-6">
          <button
            type="button"
            onClick={() => navigate({ to: '/demo' })}
            className="text-content-primary-soft hover:underline text-sm inline-flex items-center gap-1"
          >
            Just exploring? Try the live demo — no database needed
            <Icon name="arrow-right" label="" className="w-3.5 h-3.5" />
          </button>
          <Text level="caption" className="text-content-layout-3 text-center">
            You can add an AI key later, only when a feature needs it.
          </Text>
        </VStack>
      </div>
    </div>
  )
}
