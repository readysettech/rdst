/**
 * Configure page - Manage database connection targets
 */

import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { ErrorState } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createFileRoute,
  useLocation,
  useNavigate,
  useRouter,
} from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AwsConnectionPanel } from '../components/aws/AwsConnectionPanel'
import {
  AddTargetsDrawer,
  ConfigureForm,
  ConfigureTargetList,
  DevSettingsSection,
  MoveToGroupDialog,
  SettingsSection,
  TargetGroupView,
} from '../components/configure'
import { type AddTab, parseAddTab } from '../components/configure/addTabs'
import { DigitalOceanConnectionPanel } from '../components/digitalocean/DigitalOceanConnectionPanel'
import { EnvSecretsDialog } from '../components/EnvSecretsDialog'
import { NeonConnectionPanel } from '../components/neon/NeonConnectionPanel'
import { SupabaseConnectionPanel } from '../components/supabase/SupabaseConnectionPanel'
import { TrialRegistrationDialog } from '../components/TrialRegistrationDialog'
import {
  type AnthropicKeyValidation,
  type EnvRequirement,
  resetLocalData,
} from '../lib/api'
import {
  clearAllBackgroundRuns,
  startBootstrapRun,
} from '../lib/backgroundRuns'
import { classifyError, TRIAL_EXHAUSTED_MESSAGE } from '../lib/errorContract'
import { invalidateTargetQueries } from '../lib/targetQueries'
import { isSshErrorCategory, sshErrorCopy } from '../lib/sshErrors'
import { fetchTunnelStatuses, testTunnel } from '../lib/tunnels'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import { useAnthropicValidity } from '../lib/useAnthropicValidity'
import { useConfigure } from '../lib/useConfigure'
import { fetchFleetTargets, useFleetStatus } from '../lib/useFleet'
import { useSystemStatus } from '../lib/useSystemStatus'
import type {
  ConfigureFormData,
  ConfigureTarget,
  ConfigureTargetDetail,
} from '../types/configure'

// Deep-link params for the routable notices (configure-and-identity return
// trips): `edit` opens a connection's edit form, `section=ai` focuses the AI
// key card, `returnTo` sends the user back to the feature after the fix, and
// `add` opens the Add Targets drawer on that source tab (the relocation target
// for the retired /fleet route).
// Parse-only — never throw here (keeps the app shell intact; B1 lesson).
type ConfigureSearch = {
  edit?: string
  section?: 'ai'
  returnTo?: string
  add?: AddTab
}

export const Route = createFileRoute('/configure')({
  validateSearch: (search: Record<string, unknown>): ConfigureSearch => ({
    edit: typeof search.edit === 'string' ? search.edit : undefined,
    section: search.section === 'ai' ? 'ai' : undefined,
    returnTo: typeof search.returnTo === 'string' ? search.returnTo : undefined,
    add: parseAddTab(search.add),
  }),
  component: ConfigurePage,
})

/** One segment of the list/groups switch above the connection list. */
function ViewToggle({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="h-8 px-3 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
        data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
        data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
      data-active={active}
    >
      {label}
    </button>
  )
}

function keyValidationVariant(
  v: AnthropicKeyValidation
): 'positive' | 'negative' | 'warning' {
  if (v.valid) return 'positive'
  if (v.reason === 'rejected' || v.reason === 'exhausted') return 'negative'
  return 'warning'
}

function keyValidationLabel(v: AnthropicKeyValidation): string {
  const isTrial = v.source === 'trial' || v.source === 'trial_exhausted'
  if (v.valid) {
    return isTrial
      ? 'Trial token verified.'
      : 'API key verified.'
  }
  switch (v.reason) {
    case 'exhausted':
      return TRIAL_EXHAUSTED_MESSAGE
    case 'rejected':
      return isTrial
        ? 'Trial token rejected by the Readyset trial service. Request a new one or add your own key.'
        : 'Key rejected by Anthropic. Update it with a valid key.'
    case 'no_key':
      return 'No Anthropic key or trial token is configured yet.'
    default:
      return isTrial
        ? "Couldn't reach the Readyset trial service to verify your token. Check your connection and try again."
        : "Couldn't reach Anthropic to verify the key. Check your connection and try again."
  }
}

// Scroll a Settings section (by id) into view for a deep-link. Fires twice —
// the section entrance animations and async data (targets, key status) settle
// after the first frame and grow the sections above the target, so a single
// scroll under-shoots; the second pass re-aligns once layout is stable. Returns
// a cleanup that cancels pending timers. The scroll container is the app shell's
// inner overflow-auto region, which `scrollIntoView` resolves automatically.
function scrollSectionIntoView(id: string): () => void {
  const scroll = () =>
    document
      .getElementById(id)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const first = setTimeout(scroll, 300)
  const second = setTimeout(scroll, 750)
  return () => {
    clearTimeout(first)
    clearTimeout(second)
  }
}

function ConfigurePage() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const navigate = useNavigate()
  const search = Route.useSearch()
  // Router-reactive hash (TanStack strips the leading "#"): drives the
  // /configure#dev deep-link so the effect fires once the redirect's hash is
  // applied, not racily at mount.
  const locationHash = useLocation({ select: (l) => l.hash })
  const [showForm, setShowForm] = useState(false)
  const [editingTarget, setEditingTarget] =
    useState<ConfigureTargetDetail | null>(null)
  const [showAnthropicDialog, setShowAnthropicDialog] = useState(false)
  const [passwordDialogTarget, setPasswordDialogTarget] = useState<
    string | null
  >(null)
  const [showTrialDialog, setShowTrialDialog] = useState(false)
  // Two-click destructive reset: first click arms with an explicit warning,
  // second click deletes; arming auto-expires.
  const [resetArmed, setResetArmed] = useState(false)
  useEffect(() => {
    if (!resetArmed) return
    const id = setTimeout(() => setResetArmed(false), 8000)
    return () => clearTimeout(id)
  }, [resetArmed])
  const resetMutation = useMutation({
    mutationFn: resetLocalData,
    // A wiped ~/.rdst invalidates every piece of client state at once; a
    // full reload lands on the fresh-install experience. Background-run
    // records must go first, or the reloaded page would rehydrate chips for
    // jobs the server just cancelled.
    onSuccess: () => {
      clearAllBackgroundRuns()
      window.location.reload()
    },
    onError: () => setResetArmed(false),
  })
  const handleResetLocalData = () => {
    if (!resetArmed) {
      setResetArmed(true)
      return
    }
    resetMutation.mutate()
  }
  // Which connection is being tested (drives the inline per-row spinner).
  const [testingTarget, setTestingTarget] = useState<string | null>(null)
  const [testingTunnel, setTestingTunnel] = useState<string | null>(null)
  const [testingForm, setTestingForm] = useState(false)
  const deepLinkHandledRef = useRef(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<AddTab>('aws')
  const [moveTarget, setMoveTarget] = useState<ConfigureTarget | null>(null)
  const [view, setView] = useState<'list' | 'groups'>('groups')

  const {
    listTargets,
    getTarget,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    cancel: cancelConfigure,
    targets,
    connectionTestResult,
    error,
    loading,
  } = useConfigure()

  // Groups and tags are fleet-side attributes; the row set and its CRUD stay
  // with useConfigure, and this list is joined by name purely for the labels.
  const { data: fleetTargets } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
  })
  const fleetByName = useMemo(
    () =>
      new Map(
        (fleetTargets?.members ?? []).map((member) => [member.name, member])
      ),
    [fleetTargets]
  )
  const groupOf = useCallback(
    (target: ConfigureTarget) => fleetByName.get(target.name)?.group ?? null,
    [fleetByName]
  )
  const groups = useMemo(() => {
    const seen = new Set<string>()
    for (const target of targets) {
      const group = groupOf(target)
      if (group) seen.add(group)
    }
    return [...seen]
  }, [targets, groupOf])

  const {
    check,
    state: connectivityState,
    results: connectivity,
    error: connectivityError,
  } = useFleetStatus()
  const checking = connectivityState === 'running'
  const hasSshTargets = targets.some((target) => Boolean(target.ssh))
  const tunnelStatusQuery = useQuery({
    queryKey: ['tunnel-status'],
    queryFn: fetchTunnelStatuses,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
    enabled: hasSshTargets,
  })
  const tunnelStatuses = useMemo(
    () =>
      Object.fromEntries(
        (tunnelStatusQuery.data ?? []).map((status) => [status.target, status])
      ),
    [tunnelStatusQuery.data]
  )
  // The connectivity endpoint only knows fleet targets, so Readyset-proxy rows
  // are never checkable: name them explicitly rather than sweeping blind. Until
  // the fleet list answers, every row stays offerable so a slow or failing
  // lookup can't strand the whole section.
  const checkableNames = useMemo(
    () =>
      targets
        .map(({ name }) => name)
        .filter((name) => !fleetTargets || fleetByName.has(name)),
    [targets, fleetTargets, fleetByName]
  )
  const checkableTargets = useMemo(
    () => (fleetTargets ? new Set(checkableNames) : undefined),
    [fleetTargets, checkableNames]
  )
  const refreshTunnelStatuses = useCallback(async () => {
    if (hasSshTargets) await tunnelStatusQuery.refetch()
  }, [hasSshTargets, tunnelStatusQuery.refetch])
  const checkAll = useCallback(async () => {
    if (checkableNames.length === 0) return
    await check(undefined, checkableNames)
    await refreshTunnelStatuses()
  }, [check, checkableNames, refreshTunnelStatuses])

  // Sweep connectivity once per mount, as soon as the targets are known.
  const didAutoCheck = useRef(false)
  useEffect(() => {
    if (didAutoCheck.current || checkableNames.length === 0) return
    didAutoCheck.current = true
    checkAll()
  }, [checkableNames.length, checkAll])

  const { data: statusData } = useSystemStatus()
  const dataDirectory = statusData?.data_directory ?? null

  const {
    envRequirements,
    anthropicRequirement,
    isTrialSource: trialSourceDetected,
    trialStatus,
  } = useTrialSource()

  const isTrialExhausted =
    trialSourceDetected &&
    (trialStatus?.status === 'exhausted' || trialStatus?.active === false)
  const showAnthropicAction = Boolean(anthropicRequirement)

  // Presence vs. validity: a saved key can still be stale/rejected. Probe
  // only when a real Anthropic key is the active source - a trial token is
  // not an Anthropic key, so testing it against Anthropic would always
  // "reject" and the verdict would be meaningless.
  const hasAnthropicKey =
    Boolean(anthropicRequirement?.satisfied) &&
    !trialSourceDetected &&
    !isTrialExhausted
  const keyValidityQuery = useAnthropicValidity(hasAnthropicKey)
  const keyValidity = keyValidityQuery.data
  const keyChecking =
    hasAnthropicKey && keyValidityQuery.isFetching && !keyValidity
  // Guarded on hasAnthropicKey so a verdict cached before switching to trial
  // credits can't keep the rejected state alive.
  const keyRejected =
    hasAnthropicKey &&
    keyValidity?.valid === false &&
    keyValidity.reason === 'rejected'

  const anthropicStatusTitle = isTrialExhausted
    ? 'Trial Credits Exhausted'
    : keyRejected
      ? 'Anthropic Key Rejected'
      : keyChecking
        ? 'Checking Anthropic Key…'
        : trialSourceDetected
          ? 'Trial Credits In Use'
          : anthropicRequirement?.satisfied
            ? 'Anthropic API Key Configured'
            : 'Anthropic API Key Missing'

  const anthropicStatusDescription = isTrialExhausted
    ? TRIAL_EXHAUSTED_MESSAGE
    : keyRejected
      ? 'Anthropic rejected this key. Update it with a valid key to keep AI analysis working.'
      : keyChecking
        ? 'Verifying the key with Anthropic…'
        : trialSourceDetected
          ? 'You can add or update your Anthropic API key here so AI analysis keeps working.'
          : anthropicRequirement?.satisfied
            ? 'You can replace your Anthropic API key here.'
            : 'Set your own Anthropic API key to avoid interruptions and keep using AI analysis.'
  const anthropicDialogRequirements: EnvRequirement[] = useMemo(() => {
    if (!anthropicRequirement) {
      return []
    }
    if (
      !anthropicRequirement.accepted_names ||
      anthropicRequirement.accepted_names.length === 0
    ) {
      return [
        {
          ...anthropicRequirement,
          accepted_names: ['ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
        },
      ]
    }
    return [anthropicRequirement]
  }, [anthropicRequirement])
  // Trigger copy matches the dialog it opens: "Update key" when a key/trial is
  // already in play, "Set key" on first setup. (configure-settings Copy #1)
  const anthropicButtonLabel =
    isTrialExhausted || trialSourceDetected || anthropicRequirement?.satisfied
      ? 'Update key'
      : 'Set key'

  // Load targets on mount
  useEffect(() => {
    listTargets()
  }, [listTargets])

  // Deep-links from the routable notices: open a connection's edit form
  // (?edit=name) or focus the AI key card (?section=ai). Runs once.
  useEffect(() => {
    if (deepLinkHandledRef.current) return
    if (search.edit) {
      deepLinkHandledRef.current = true
      const editTarget = search.edit
      void (async () => {
        const target = await getTarget(editTarget)
        if (target) {
          setEditingTarget(target)
          setShowForm(true)
        }
      })()
    } else if (search.section === 'ai') {
      deepLinkHandledRef.current = true
      setShowAnthropicDialog(true)
    }
  }, [search.edit, search.section, getTarget])

  // Deep-link ?section=ai must LAND on the AI keys section (not just open the
  // dialog): scroll the section into view so the user (and the dialog's return
  // trip) resolve against the right zone. Deferred past the section entrance
  // animations so the target's position has settled. [C-04, USE-006]
  useEffect(() => {
    if (search.section !== 'ai' || !showAnthropicAction) return
    return scrollSectionIntoView('ai')
  }, [search.section, showAnthropicAction])

  // Deep-link /configure#dev — the relocation target for the retired
  // /dev-settings route. Land on the Developer settings section so old deep
  // links survive the merge. (Dev-only section; a no-op in prod.)
  useEffect(() => {
    if (locationHash !== 'dev') return
    return scrollSectionIntoView('dev')
  }, [locationHash])

  // Deep-link /configure#connections — where the retired /fleet route lands.
  useEffect(() => {
    if (locationHash !== 'connections') return
    return scrollSectionIntoView('connections')
  }, [locationHash])

  // ?add=aws|supabase|neon|digitalocean|csv (onboarding's discovery link, or
  // an old /fleet?add= link) opens the Add Targets drawer directly on that
  // tab. Runs once.
  const addDeepLinkHandledRef = useRef(false)
  useEffect(() => {
    if (!search.add || addDeepLinkHandledRef.current) return
    addDeepLinkHandledRef.current = true
    setDrawerTab(search.add)
    setDrawerOpen(true)
  }, [search.add])

  // After a fix reached via a routable notice, send the user back to the
  // feature they came from so the flow resumes in place. [USE-021, USE-077]
  const returnToFeature = () => {
    if (search.returnTo) {
      router.history.push(search.returnTo)
    }
  }

  const handleAddClick = () => {
    setEditingTarget(null)
    setShowForm(true)
  }

  const handleEditClick = async (targetName: string) => {
    const target = await getTarget(targetName)
    if (!target) {
      return
    }
    setEditingTarget(target)
    setShowForm(true)
  }

  const handleFormSubmit = async (data: ConfigureFormData) => {
    try {
      if (editingTarget) {
        await updateTarget(editingTarget.name, data)
      } else {
        await addTarget(data)
      }
    } catch {
      return
    }
    if (!editingTarget) {
      // New target: kick off the background bootstrap; it never throws, and
      // the sidebar chip tracks it.
      startBootstrapRun(data.name)
    }
    if (data.ssh || editingTarget?.ssh) await tunnelStatusQuery.refetch()
    setShowForm(false)
    setEditingTarget(null)
    // If we arrived here to fix a connection, resume the feature we came from.
    returnToFeature()
  }

  const handleFormCancel = () => {
    cancelConfigure()
    setTestingForm(false)
    setShowForm(false)
    setEditingTarget(null)
  }

  const handleFormTest = async (data: ConfigureFormData) => {
    setTestingForm(true)
    try {
      const result = await testConnection(data.name.trim() || 'form-test', data)
      await tunnelStatusQuery.refetch()
      return result?.connected ?? false
    } finally {
      setTestingForm(false)
    }
  }

  // Confirmation is handled by the styled ConfirmDialog inside the list.
  const handleDelete = async (targetName: string) => {
    await removeTarget(targetName)
  }

  const handleSetDefault = async (targetName: string) => {
    await setDefaultTarget(targetName)
  }

  const handleTest = async (targetName: string) => {
    setTestingTarget(targetName)
    try {
      await check(undefined, [targetName])
    } finally {
      await refreshTunnelStatuses()
      setTestingTarget(null)
    }
  }

  const handleTunnelTest = async (targetName: string) => {
    setTestingTunnel(targetName)
    try {
      const result = await testTunnel(targetName)
      await tunnelStatusQuery.refetch()
      toast({
        title: result.ok ? 'Tunnel test passed' : 'Tunnel test failed',
        description:
          !result.ok && isSshErrorCategory(result.category)
            ? sshErrorCopy({
                category: result.category,
                message: result.message,
                target: targetName,
              })
            : result.message,
        variant: result.ok ? 'positive' : 'negative',
      })
    } catch (caught) {
      toast({
        title: 'Tunnel test failed',
        description:
          caught instanceof Error
            ? caught.message
            : 'Could not test the tunnel.',
        variant: 'negative',
      })
    } finally {
      setTestingTunnel(null)
    }
  }

  // A saved password changes what the row says about itself: `has_password`
  // rides on the target list, and this page holds that list plus its
  // connectivity verdicts in state the dialog does not own. Republish the rows
  // the same way the drawer does, then re-check exactly the saved target.
  const publishSavedPassword = async (targetName: string) => {
    await invalidateTargetQueries(queryClient)
    await listTargets()
    await handleTest(targetName)
  }

  const openDrawer = (tab: AddTab) => {
    setDrawerTab(tab)
    setDrawerOpen(true)
  }
  const openProviderPicker = () => setDrawerOpen(true)

  const passwordDialogRequirements = useMemo(
    () =>
      (envRequirements?.requirements ?? []).filter(
        (requirement) =>
          requirement.kind === 'target_password' &&
          requirement.target === passwordDialogTarget
      ),
    [envRequirements, passwordDialogTarget]
  )

  const handleTestKey = () => {
    void keyValidityQuery.refetch()
  }

  const countsLabel = `${targets.length} ${
    targets.length === 1 ? 'target' : 'targets'
  } · ${groups.length} ${groups.length === 1 ? 'group' : 'groups'}`

  // Both views render the same row-cards; the grouped view only nests them
  // under collapsible headers.
  const renderTargets = (rows: ConfigureTarget[]) => (
    <ConfigureTargetList
      targets={rows}
      onEdit={(target) => void handleEditClick(target.name)}
      onTest={(name) => void handleTest(name)}
      onDelete={handleDelete}
      onSetDefault={handleSetDefault}
      onAdd={handleAddClick}
      onDiscover={openProviderPicker}
      onMoveToGroup={setMoveTarget}
      isLoading={loading}
      connectivity={connectivity}
      checkableTargets={checkableTargets}
      testingTargetName={testingTarget}
      onSetPassword={(target) => setPasswordDialogTarget(target.name)}
      onRetryConnection={async (target) => {
        const checked = await check(undefined, [target.name])
        await refreshTunnelStatuses()
        return checked[target.name]?.status === 'ok'
      }}
      tunnelStatuses={tunnelStatuses}
      testingTunnelName={testingTunnel}
      onTestTunnel={(name) => void handleTunnelTest(name)}
    />
  )

  const editingInitialData = editingTarget
    ? {
        name: editingTarget.name,
        engine: editingTarget.engine,
        host: editingTarget.host,
        port: editingTarget.port,
        database: editingTarget.database,
        user: editingTarget.user,
        password_env: editingTarget.password_env,
        tls: editingTarget.tls,
        read_only: editingTarget.read_only,
        ssh: editingTarget.ssh,
      }
    : undefined

  return (
    <div className="space-y-6 w-full">
      {/* Page header — title + subtitle only. The Add action moves into the
          Database connections section (owner: "Add target → its section").
          P1's "Settings" h1 + generalized subtitle are preserved. [USE-041] */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon
              name="settings"
              label="Configure"
              className="w-6 h-6 text-content-primary-soft"
            />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Settings
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Database connections, API keys, and privacy.
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {/* Sectioned single-column layout (owner reference image.png): a hairline
          rule under the header, then each section is a nameable zone separated
          by space + a rule — one column, no second sidebar (the app already has
          its own). Sections stay visible at zero targets now that each is a
          clearly-labelled zone; the empty-state hero lives INSIDE the first
          section. [VIS-113, VIS-036, VIS-104, USE-097] */}
      <div className="border-t border-border-layout-1 divide-y divide-border-layout-1">
        {/* ── Database connections — primary section; Add moves in here ── */}
        <SettingsSection
          className="pt-8 pb-10"
          id="connections"
          title="Database connections"
          description="Add databases for RDST to analyze with a read-only user."
          action={
            !showForm ? (
              <HStack className="gap-2 items-center flex-wrap">
                <Button
                  variant="rising"
                  modifier="solid"
                  icon="add"
                  iconPosition="left"
                  label="Add Target"
                  onClick={handleAddClick}
                />
                <Button
                  variant="primary"
                  modifier="outline"
                  icon="search"
                  iconPosition="left"
                  label="Discover & import"
                  onClick={openProviderPicker}
                />
              </HStack>
            ) : undefined
          }
        >
          <VStack className="gap-4 items-stretch w-full">
            {/* Error */}
            <Show when={!!error}>
              <m.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
              >
                <ErrorState
                  errorClass={classifyError({ code: '', message: error ?? '' })}
                  title="Connection action failed"
                  message={
                    error ??
                    'The last action on your connections could not complete.'
                  }
                  trustworthy="Your saved connections are unchanged."
                  onRetry={() => void listTargets()}
                  retryLabel="Reload connections"
                />
              </m.div>
            </Show>

            <Show when={!!connectivityError}>
              <ErrorState
                errorClass={classifyError({
                  code: '',
                  message: connectivityError ?? '',
                })}
                title="Connectivity check failed"
                message={
                  connectivityError ?? 'The connectivity check could not run.'
                }
                trustworthy="The connection list is unaffected — only the live reachability of each row is stale."
                onRetry={checkAll}
                retryLabel="Check again"
              />
            </Show>

            {/* Provider sessions are otherwise only visible inside the discover
                drawer, so a signed-in account looks like no account at all.
                Each row states its own connection, and stays quiet when the
                user has never signed into that provider. */}
            <Show when={targets.length > 0}>
              <VStack className="gap-1.5 items-stretch">
                <Text level="caption" className="text-content-layout-3">
                  Connections
                </Text>
                <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3 items-stretch">
                  <AwsConnectionPanel
                    compact
                    onSignIn={() => openDrawer('aws')}
                  />
                  <SupabaseConnectionPanel
                    compact
                    onSignIn={() => openDrawer('supabase')}
                  />
                  <NeonConnectionPanel
                    compact
                    onSignIn={() => openDrawer('neon')}
                  />
                  <DigitalOceanConnectionPanel
                    compact
                    onSignIn={() => openDrawer('digitalocean')}
                  />
                </div>
              </VStack>
            </Show>

            {/* Form replaces the list while adding/editing. */}
            <Show when={showForm}>
              <m.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
              >
                <ConfigureForm
                  key={
                    editingTarget ? `edit-${editingTarget.name}` : 'new-target'
                  }
                  initialData={editingInitialData}
                  onSubmit={handleFormSubmit}
                  onTest={handleFormTest}
                  onCancel={handleFormCancel}
                  isLoading={loading}
                  isTesting={testingForm}
                  testResult={connectionTestResult}
                />
              </m.div>
            </Show>

            {/* Toolbar — the view switch appears only once a group exists, so a
                flat install never grows an "Ungrouped" band it can't use. */}
            <Show when={!showForm && targets.length > 0}>
              <HStack className="justify-between items-center gap-3 flex-wrap">
                <HStack className="gap-3 items-center flex-wrap">
                  <Show when={groups.length > 0}>
                    <HStack
                      className="gap-1"
                      role="group"
                      aria-label="Target view"
                    >
                      <ViewToggle
                        active={view === 'list'}
                        label="List"
                        onClick={() => setView('list')}
                      />
                      <ViewToggle
                        active={view === 'groups'}
                        label="Groups"
                        onClick={() => setView('groups')}
                      />
                    </HStack>
                  </Show>
                  <Text level="caption" className="text-content-layout-3">
                    {countsLabel}
                  </Text>
                </HStack>
                <Button
                  variant="primary"
                  modifier="outline"
                  size="small"
                  icon="connect"
                  iconPosition="left"
                  label="Check all"
                  loading={checking}
                  disabled={checking || checkableNames.length === 0}
                  onClick={checkAll}
                />
              </HStack>
            </Show>

            {/* The list — and, at zero targets, its polished hero empty-state
                (the "Add your first connection" surface) render here. [VIS-102].
                Connectivity and the unreachable notice render per row. */}
            <Show when={!showForm}>
              <m.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.1 }}
              >
                {view === 'groups' && targets.length > 0 ? (
                  <TargetGroupView
                    targets={targets}
                    groupOf={groupOf}
                    renderTargets={renderTargets}
                    onHealthCheckGroup={(group) =>
                      void navigate({
                        to: '/audit',
                        search: { scope: 'group', group },
                      })
                    }
                  />
                ) : (
                  renderTargets(targets)
                )}
              </m.div>
            </Show>
          </VStack>
        </SettingsSection>

        {/* ── AI keys — its own section (owner #4: no "just-floating" key card;
            deep-link target id="ai" for ?section=ai). ── */}
        {showAnthropicAction ? (
          <SettingsSection
            className="py-10"
            id="ai"
            title="AI keys"
            description="The Anthropic API key that powers Analyze, Ask, and Health Check insights."
          >
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4 space-y-3">
                <HStack className="items-start gap-3 justify-between">
                  <HStack className="gap-3 items-start">
                    <div
                      className={`w-9 h-9 rounded-xl flex items-center justify-center ${keyRejected ? 'bg-surface-negative-soft' : 'bg-surface-warning-soft'}`}
                    >
                      <Icon
                        name="key"
                        label="Anthropic"
                        className={`w-4 h-4 ${keyRejected ? 'text-content-negative-soft' : 'text-content-warning-soft'}`}
                      />
                    </div>
                    <VStack className="gap-0.5 items-start">
                      <Text
                        level="label-small"
                        className="text-content-layout-1"
                      >
                        {anthropicStatusTitle}
                      </Text>
                      <Text
                        level="body-small"
                        className="text-content-layout-3"
                      >
                        {anthropicStatusDescription}
                      </Text>
                      {trialSourceDetected &&
                      trialStatus?.remaining_tokens_display &&
                      trialStatus?.limit_tokens_display ? (
                        <Text level="caption" className="text-content-layout-3">
                          Trial balance: {trialStatus.remaining_tokens_display}{' '}
                          / {trialStatus.limit_tokens_display}
                        </Text>
                      ) : null}
                    </VStack>
                  </HStack>
                  <HStack className="gap-2 items-center">
                    <Show when={hasAnthropicKey}>
                      <Button
                        variant="primary"
                        modifier="ghost"
                        label={
                          keyValidityQuery.isFetching ? 'Testing…' : 'Test key'
                        }
                        disabled={keyValidityQuery.isFetching}
                        onClick={handleTestKey}
                      />
                    </Show>
                    <Show when={!trialSourceDetected}>
                      <Button
                        variant="primary"
                        modifier="ghost"
                        label="Use trial credits"
                        icon="sparkles"
                        iconPosition="left"
                        onClick={() => setShowTrialDialog(true)}
                      />
                    </Show>
                    <Button
                      variant="primary"
                      modifier="outline"
                      label={anthropicButtonLabel}
                      icon="key"
                      iconPosition="left"
                      onClick={() => setShowAnthropicDialog(true)}
                    />
                  </HStack>
                </HStack>
                {/* While a (re-)test is in flight, keep an explicit "Checking
                    key…" line in the status area instead of blinking to blank —
                    so the Test-key loop reads button → checking → fresh verdict
                    on every press, even when re-validating an already-valid key.
                    Once it settles, the verdict Alert shows the result; a failed
                    test stays visible as a negative/warning notice. [USE-008/071,
                    VIS-119] */}
                <Show when={hasAnthropicKey && keyValidityQuery.isFetching}>
                  <Alert
                    variant="informative"
                    modifier="outline"
                    loading
                    label="Checking key with Anthropic…"
                  />
                </Show>
                <Show
                  when={Boolean(keyValidity) && !keyValidityQuery.isFetching}
                >
                  {keyValidity ? (
                    <Alert
                      variant={keyValidationVariant(keyValidity)}
                      modifier="outline"
                      label={keyValidationLabel(keyValidity)}
                    />
                  ) : null}
                </Show>
              </div>
            </m.div>
          </SettingsSection>
        ) : null}

        {/* ── Storage & privacy — OPEN card, no collapse (owner decision #5;
            undoes C-09's disclosure, keeps every line of content). ── */}
        {dataDirectory ? (
          <SettingsSection
            className="py-10"
            title="Storage & privacy"
            description="Where your data lives, and what stays local."
          >
            <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4">
              <VStack className="gap-2 items-start">
                <Text level="body-small" className="text-content-layout-3">
                  All RDST data is stored locally on your machine. Nothing is
                  sent to external servers.
                </Text>
                <HStack className="gap-2 items-center">
                  <code className="text-xs bg-surface-raised px-2 py-1 rounded font-mono text-content-layout-2">
                    {dataDirectory}
                  </code>
                  <CopyButton text={dataDirectory || ''} />
                </HStack>
                <Text level="caption" className="text-content-layout-3">
                  Contains connection configs, saved queries, semantic layer,
                  and analysis history. Passwords are stored in your system
                  keyring, never in plain text.
                </Text>
                <HStack className="gap-3 items-center pt-2">
                  <Button
                    variant="negative"
                    modifier="outline"
                    label={
                      resetArmed
                        ? 'Click again to permanently remove'
                        : 'Remove all local data'
                    }
                    disabled={resetMutation.isPending}
                    onClick={handleResetLocalData}
                  />
                  {resetArmed ? (
                    <Text
                      level="caption"
                      className="text-content-negative-soft"
                    >
                      Deletes {dataDirectory} and stored keys. Your trial
                      registration is kept server-side — re-enter your email to
                      recover your token. ~/.ssh is unchanged.
                    </Text>
                  ) : null}
                </HStack>
              </VStack>
            </div>
          </SettingsSection>
        ) : null}

        {/* ── Developer settings — merged from the retired /dev-settings route
            (owner: "Dev Settings → Settings içine"). DEV-only, last section;
            deep-link target id="dev" for /configure#dev. ── */}
        {import.meta.env.DEV ? (
          <SettingsSection
            className="py-10"
            id="dev"
            title="Developer settings"
            description="Tools and utilities available only in development mode."
          >
            <DevSettingsSection />
          </SettingsSection>
        ) : null}
      </div>

      <EnvSecretsDialog
        isOpen={passwordDialogTarget !== null}
        onClose={() => setPasswordDialogTarget(null)}
        requirements={passwordDialogRequirements}
        keyringAvailable={Boolean(envRequirements?.keyring_available)}
        onSuccess={() => {
          const target = passwordDialogTarget
          setPasswordDialogTarget(null)
          if (target) void publishSavedPassword(target)
        }}
      />

      <AddTargetsDrawer
        open={drawerOpen}
        initialTab={drawerTab}
        onClose={() => setDrawerOpen(false)}
        onTargetsAdded={() => void listTargets()}
        onCredentialsClosed={checkAll}
        onRecheckTargets={(names) => {
          if (names.length > 0) {
            void (async () => {
              await check(undefined, names)
              await refreshTunnelStatuses()
            })()
          }
        }}
      />

      <MoveToGroupDialog
        target={
          moveTarget
            ? { name: moveTarget.name, group: groupOf(moveTarget) }
            : null
        }
        groups={groups}
        onClose={() => setMoveTarget(null)}
      />

      <EnvSecretsDialog
        isOpen={showAnthropicDialog}
        onClose={() => setShowAnthropicDialog(false)}
        requirements={anthropicDialogRequirements}
        showManualAnthropicInput
        keyringAvailable={Boolean(envRequirements?.keyring_available)}
        onTrialRegister={() => setShowTrialDialog(true)}
        trialActionLabel={
          trialSourceDetected
            ? 'Email me my trial token'
            : "Don't have a key? Claim free trial credits"
        }
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          // Re-check right away so the status line reflects the new key even
          // while the requirements refresh is still in flight.
          void keyValidityQuery.refetch()
          // If a routable "needs a key" notice sent us here, resume the feature.
          returnToFeature()
        }}
      />

      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          returnToFeature()
        }}
      />
    </div>
  )
}
