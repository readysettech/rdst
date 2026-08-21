import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouter } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { useConfigure } from '../../lib/useConfigure'
import { useOnboarding } from '../../lib/useOnboarding'
import { VALUE_PROPOSITION } from '../../lib/valueProposition'
import type { ConfigureFormData } from '../../types/configure'
import { AnimatedSurfaceBackdrop } from '../AnimatedSurfaceBackdrop'
import { ConfigureForm } from '../configure'
import type { AddTab } from '../configure/addTabs'
import {
  AwsLogo,
  DigitalOceanLogo,
  NeonLogo,
  SupabaseLogo,
} from '../providers/ProviderLogos'

// The provider-first paths, in the order they read on the page. They all read
// the same way so no provider looks like the afterthought.
const PROVIDER_CARDS = [
  {
    tab: 'aws',
    logo: <AwsLogo size={16} className="text-content-layout-2" />,
    label: 'AWS',
  },
  { tab: 'supabase', logo: <SupabaseLogo size={16} />, label: 'Supabase' },
  { tab: 'neon', logo: <NeonLogo size={16} />, label: 'Neon' },
  {
    tab: 'digitalocean',
    logo: <DigitalOceanLogo size={16} />,
    label: 'DigitalOcean',
  },
] as const satisfies readonly {
  tab: AddTab
  logo: ReactNode
  label: string
}[]

/** Compact one-click hand-off into a provider's discovery drawer. */
function ProviderDiscoverTile({
  logo,
  label,
  onDiscover,
}: {
  logo: ReactNode
  label: string
  onDiscover: () => void
}) {
  return (
    <Button
      type="button"
      label={label}
      modifier="outline"
      fullWidth
      onClick={onDiscover}
      classMerge="h-11 justify-start rounded-xl border-border-layout-1 bg-surface-raised px-3 text-content-layout-1 shadow-elevation-1 hover:border-border-primary-solid hover:bg-surface-raised"
      innerClassName="gap-2"
    >
      <span className="shrink-0 inline-flex" aria-hidden="true">
        {logo}
      </span>
    </Button>
  )
}

/**
 * First-run "Connect your database" — a single, exitable page that replaces the
 * four-step `fixed inset-0` wizard (onboarding-and-first-run). One job: point
 * RDST at a database. The AI key is deferred to just-in-time (never asked here),
 * the demo is a primary zero-setup product path, and the page is a normal route
 * inside the app shell — skippable, never a takeover.
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
  const { addTarget, setDefaultTarget, cancel, loading } = useConfigure()
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
    // Kick off schema/annotation bootstrap while the user lands in the app.
    // Readyset itself starts lazily only when the user runs a comparison.
    startBootstrapRun(data.name)
    toast({ title: `Connected to ${data.name}`, variant: 'positive' })
    finishToHome()
  }

  const skip = () => {
    cancel()
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
        <Button
          variant="primary"
          modifier="link"
          size="small"
          label="Skip for now"
          icon="arrow-right"
          iconPosition="right"
          onClick={skip}
        />
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
            {fromDemo ? 'Connect your database' : 'Start with Readyset'}
          </Text>
          <Text
            level="body-medium"
            className="text-content-layout-2 leading-relaxed"
          >
            {fromDemo
              ? 'Use a read-only database user. RDST runs EXPLAIN, schema, index, and performance-statistics queries against PostgreSQL or MySQL.'
              : // Collapses into the sidebar's one value-proposition line (C1 /
                // D-5) instead of re-explaining the two paths below, which the
                // demo hero card and "Or connect your own database" divider
                // already carry.
                VALUE_PROPOSITION}
          </Text>
        </VStack>

        {!fromDemo && (
          <div className="relative mb-8 overflow-hidden rounded-2xl bg-surface-rising-solid shadow-elevation-2">
            <AnimatedSurfaceBackdrop palette="purple" />
            <div className="relative flex flex-col gap-6 p-6 tablet:flex-row tablet:items-end tablet:justify-between">
              <VStack className="max-w-md items-start gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-layout-1/20">
                  <Icon
                    name="querypilot"
                    label=""
                    className="h-6 w-6 text-content-rising-solid"
                  />
                </div>
                <VStack className="items-start gap-1">
                  <Text
                    level="headline-4"
                    className="text-content-rising-solid"
                  >
                    Try the live demo
                  </Text>
                  <Text
                    level="body-small"
                    className="text-content-rising-solid/80"
                  >
                    Run a guided comparison on a prepared workload and inspect
                    the measured Readyset speedup. No database or setup needed.
                  </Text>
                </VStack>
              </VStack>
              <Button
                variant="primary"
                modifier="solid"
                label="Launch the demo"
                icon="arrow-right"
                iconPosition="right"
                className="shrink-0 bg-surface-layout-1 text-content-layout-1 hover:bg-surface-layout-2 active:bg-surface-layout-2"
                onClick={() => navigate({ to: '/demo' })}
              />
            </div>
          </div>
        )}

        {!fromDemo && (
          <HStack className="mb-6 items-center gap-3">
            <div className="h-px flex-1 bg-border-layout-1" />
            <Text level="caption" className="text-content-layout-3">
              Or connect your own database
            </Text>
            <div className="h-px flex-1 bg-border-layout-1" />
          </HStack>
        )}

        {/* Provider-first paths, compact: one click into the discovery drawer
            pre-selected on that provider, kept to two rows so the manual form
            below stays visible without scrolling. */}
        <VStack className="gap-1.5 items-stretch mb-6">
          <Text level="caption" className="text-content-layout-3">
            Have databases at a cloud provider? Discover them
          </Text>
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2">
            {PROVIDER_CARDS.map((card) => (
              <ProviderDiscoverTile
                key={card.tab}
                logo={card.logo}
                label={card.label}
                onDiscover={() =>
                  navigate({ to: '/configure', search: { add: card.tab } })
                }
              />
            ))}
          </div>
        </VStack>

        {/* One raised card on the hero surface: the form, its inline test
            state, and its single primary CTA read as one grouped unit — no dead
            band, no floating second list card. [VIS-036, VIS-111, VIS-022] */}
        <div className="rounded-[1.25rem] shadow-elevation-1">
          <ConfigureForm
            onSubmit={handleSubmit}
            onCancel={skip}
            isLoading={loading}
            submitLabel="Test & connect"
            submitSize="large"
          />
        </div>

        <VStack className="gap-3 items-center mt-6">
          <Text level="caption" className="text-content-layout-3 text-center">
            You can add an AI key later, only when a feature needs it.
          </Text>
        </VStack>
      </div>
    </div>
  )
}
