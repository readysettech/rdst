import { Alert } from '@rs/ui-new/alert'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button, buttonStyles } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { Pressable } from '@rs/ui-new/pressable'
import { Show } from '@rs/ui-new/show'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Link,
  useLocation,
  useNavigate,
  useRouter,
} from '@tanstack/react-router'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { SlotsElsewhere } from '@/features/monitor-kit/AiMonitoringStatus'
import { useMonitoringLimit, useMonitorSettings } from '@/lib/monitor/hooks'
import { AddDatabaseDialog } from '../../components/configure/AddDatabaseDialog'
import {
  notifyDatabasesChanged,
  onDatabasesChanged,
  useOpenAddDatabase,
} from '../../components/configure/addDatabase'
import type { AddTab } from '../../components/configure/addTabs'
import { DesktopUpdateControl } from '../../components/DesktopUpdateControl'
import { EnvSecretsDialog } from '../../components/EnvSecretsDialog'
import { ReportDialog } from '../../components/ReportDialog'
import { TrialRegistrationDialog } from '../../components/TrialRegistrationDialog'
import { useTarget } from '../../hooks/useTarget'
import {
  type AnthropicKeyValidation,
  type EnvRequirement,
  fetchAccountStatus,
  logoutAccount,
  resetLocalData,
  setAiProvider,
} from '../../lib/api'
import { clearAllBackgroundRuns } from '../../lib/backgroundRuns'
import { thisComputer } from '../../lib/desktop'
import { classifyError } from '../../lib/errorContract'
import { isSshErrorCategory, sshErrorCopy } from '../../lib/sshErrors'
import { invalidateTargetQueries } from '../../lib/targetQueries'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../../lib/trialQueries'
import { fetchTunnelStatuses, testTunnel } from '../../lib/tunnels'
import { useAnthropicValidity } from '../../lib/useAnthropicValidity'
import { useConfigure } from '../../lib/useConfigure'
import { useDesktopUpdates } from '../../lib/useDesktopUpdates'
import { fetchFleetTargets, useFleetStatus } from '../../lib/useFleet'
import { useSystemStatus } from '../../lib/useSystemStatus'
import type { ConfigureTargetDetail } from '../../types/configure'
import {
  desktopNotificationsEnabled,
  onDesktopNotificationsChange,
  setDesktopNotifications,
} from '../inbox/desktopNotifications'
import { formatBytes } from '../monitor-kit/format'
import { AiKeyRouting } from './AiKeyRouting'
import { ClearKeyringRow } from './ClearKeyringRow'
import { DatabaseRows } from './DatabaseRows'
import { DISCORD_INVITE_URL, DOCS_URL } from './links'
import { PreferenceMenu } from './PreferenceMenu'
import {
  KEEP_READINGS,
  keepReadingsLabel,
  PREFERENCE_DEFAULTS,
  usePreferences,
  useUpdatePreferences,
} from './preferences'
import { SettingsGroup, SettingsRow } from './SettingsGroup'

// Deep-link params for the routable notices (configure-and-identity return
// trips): `edit` opens a connection's edit form, `section=ai` opens the AI
// key dialog, `panel` scrolls to a group, `returnTo` sends the user back to
// the feature after the fix, `add` is the add-database dialog's (it opens over
// this page on that provider, for the retired /fleet route), and `from`
// names the retired route that sent the reader here. Parse-only: never throw
// here (keeps the app shell intact).
export type ConfigureSearch = {
  edit?: string
  section?: 'ai'
  panel?: SettingsPanel
  returnTo?: string
  /** The add-database dialog's request: 1 for the form, or a provider tab. */
  add?: AddTab | 1
  from?: 'fleet' | 'dev-settings'
}

export type SettingsPanel = 'connections' | 'ai' | 'privacy' | 'developer'

const PANEL_ID: Record<SettingsPanel, string> = {
  connections: 'connections',
  ai: 'ai',
  privacy: 'privacy',
  developer: 'dev',
}

function keyValidationVariant(
  v: AnthropicKeyValidation
): 'positive' | 'negative' | 'warning' {
  if (v.valid) return 'positive'
  if (v.reason === 'rejected' || v.reason === 'exhausted') return 'negative'
  return 'warning'
}

function keyValidationLabel(v: AnthropicKeyValidation): string {
  if (v.valid) return 'API key verified.'
  switch (v.reason) {
    case 'rejected':
      return 'Key rejected by Anthropic. Update it with a valid key.'
    case 'no_key':
      return 'No Anthropic API key is configured yet.'
    default:
      return "Couldn't reach Anthropic to verify the key. Check your connection and try again."
  }
}

const ghostLink = buttonStyles({
  variant: 'primary',
  modifier: 'ghost',
  size: 'small',
}).root()

const quietLink =
  'border-b border-border-layout-2 hover:text-content-layout-1 hover:border-content-layout-2'

const subscribePush = (listener: () => void) =>
  onDesktopNotificationsChange(listener)

export function SettingsPage({ search }: { search: ConfigureSearch }) {
  const queryClient = useQueryClient()
  const router = useRouter()
  const navigate = useNavigate()
  // Router-reactive hash (TanStack strips the leading "#"): drives the
  // /configure#dev deep-link so the effect fires once the redirect's hash is
  // applied, not racily at mount.
  const locationHash = useLocation({ select: (l) => l.hash })
  const [editingTarget, setEditingTarget] =
    useState<ConfigureTargetDetail | null>(null)
  const [showAnthropicDialog, setShowAnthropicDialog] = useState(false)
  const [passwordDialogTarget, setPasswordDialogTarget] = useState<
    string | null
  >(null)
  const [showTrialDialog, setShowTrialDialog] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
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
  })
  // Which connection is being tested (drives the inline per-row spinner).
  const [testingTarget, setTestingTarget] = useState<string | null>(null)
  const deepLinkHandledRef = useRef(false)

  const {
    listTargets,
    getTarget,
    removeTarget,
    setDefaultTarget,
    state: configureState,
    targets,
    error,
  } = useConfigure()
  // 'idle' is "the list has not been asked for yet", which on first paint is
  // indistinguishable from "there are no targets" unless it is carried
  // through to the list.
  const targetsPending =
    configureState === 'idle' || configureState === 'loading'

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
  const {
    check,
    results: connectivity,
    error: connectivityError,
  } = useFleetStatus()
  const hasSshTargets = targets.some((target) => Boolean(target.ssh))
  const tunnelStatusQuery = useQuery({
    queryKey: ['tunnel-status'],
    queryFn: fetchTunnelStatuses,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
    enabled: hasSshTargets,
  })
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

  // Databases added or given credentials in the add dialog: refresh the rows and
  // re-check the connections that changed.
  useEffect(
    () =>
      onDatabasesChanged((change) => {
        void listTargets()
        if (change.recheckAll) void checkAll()
        else if (change.recheck?.length)
          void check(undefined, change.recheck).then(refreshTunnelStatuses)
      }),
    [listTargets, checkAll, check, refreshTunnelStatuses]
  )

  const statusQuery = useSystemStatus()
  const statusData = statusQuery.data
  const dataDirectory = statusData?.data_directory ?? null
  const { target: selectedTarget } = useTarget()
  const currentTarget =
    selectedTarget ?? statusData?.default_target ?? targets[0]?.name ?? null
  const monitorSettings = useMonitorSettings(currentTarget ?? '')
  const updates = useDesktopUpdates()
  const pushOn = useSyncExternalStore(
    subscribePush,
    desktopNotificationsEnabled,
    () => false
  )

  const { envRequirements, anthropicRequirement } = useTrialSource()
  const activeAiProvider =
    anthropicRequirement?.selected_provider ??
    (anthropicRequirement?.source === 'readyset_account'
      ? 'readyset'
      : anthropicRequirement?.satisfied
        ? 'claude'
        : null)
  const usingReadyset = activeAiProvider === 'readyset'
  const readysetAccountAvailable =
    anthropicRequirement?.readyset_account_connected ??
    anthropicRequirement?.source === 'readyset_account'
  const readysetAccountConnected = usingReadyset && readysetAccountAvailable
  const anthropicKeyConfigured =
    anthropicRequirement?.anthropic_key_configured ??
    (Boolean(anthropicRequirement?.satisfied) && !usingReadyset)
  const accountStatusQuery = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
  })
  const email = accountStatusQuery.data?.signed_in
    ? accountStatusQuery.data.email
    : null
  const aiProviderMutation = useMutation({
    mutationFn: setAiProvider,
    onSuccess: async ({ provider }) => {
      await invalidateTrialRelatedQueries(queryClient)
      toast({
        title:
          provider === 'readyset'
            ? 'Using the included AI'
            : 'Using your Anthropic key',
        variant: 'positive',
      })
    },
    onError: (error) => {
      toast({
        title: 'Could not switch AI provider',
        description:
          error instanceof Error ? error.message : 'Please try again.',
        variant: 'negative',
      })
    },
  })
  const accountLogoutMutation = useMutation({
    mutationFn: logoutAccount,
    onSuccess: async () => {
      await invalidateTrialRelatedQueries(queryClient)
      toast({
        title: 'Signed out of Readyset',
        description:
          'Add an Anthropic key or sign in again to use AI features.',
        variant: 'positive',
      })
    },
  })
  // Presence vs. validity: a saved key can still be stale/rejected. Probe
  // only when a real Anthropic key is the active source.
  const hasAnthropicKey =
    anthropicKeyConfigured && activeAiProvider === 'claude'
  const keyValidityQuery = useAnthropicValidity(hasAnthropicKey)
  const keyValidity = keyValidityQuery.data

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
          accepted_names: ['ANTHROPIC_API_KEY'],
        },
      ]
    }
    return [anthropicRequirement]
  }, [anthropicRequirement])

  const handleUseReadyset = () => {
    if (readysetAccountAvailable) {
      aiProviderMutation.mutate('readyset')
    } else {
      setShowTrialDialog(true)
    }
  }

  const handleHostedChange = (on: boolean) => {
    if (on) {
      handleUseReadyset()
      return
    }
    if (anthropicKeyConfigured) aiProviderMutation.mutate('claude')
    else setShowAnthropicDialog(true)
  }

  // Load targets on mount
  useEffect(() => {
    listTargets()
  }, [listTargets])

  // Deep-links from the routable notices: open a connection's edit form
  // (?edit=name) or the AI key dialog (?section=ai). Runs once.
  useEffect(() => {
    if (deepLinkHandledRef.current) return
    if (search.edit) {
      deepLinkHandledRef.current = true
      const editTarget = search.edit
      void (async () => {
        const target = await getTarget(editTarget)
        if (target) setEditingTarget(target)
      })()
    } else if (search.section === 'ai') {
      deepLinkHandledRef.current = true
      setShowAnthropicDialog(true)
    }
  }, [search.edit, search.section, getTarget])

  // ?panel= and #hash scroll to their group.
  useEffect(() => {
    const id = search.panel ? PANEL_ID[search.panel] : locationHash
    if (!id) return
    document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
  }, [search.panel, locationHash])

  // After a fix reached via a routable notice, send the user back to the
  // feature they came from so the flow resumes in place.
  const returnToFeature = () => {
    if (search.returnTo) {
      router.history.push(search.returnTo)
      return true
    }
    return false
  }

  const clearSettingsAction = (panel: SettingsPanel) => {
    void navigate({
      to: '/configure',
      search: { panel },
      replace: true,
    })
  }

  const openAddDatabase = useOpenAddDatabase()

  const handleEditClick = async (targetName: string) => {
    const target = await getTarget(targetName)
    if (!target) {
      return
    }
    setEditingTarget(target)
  }

  const handleEditSaved = async () => {
    setEditingTarget(null)
    await refreshTunnelStatuses()
    // If we arrived here to fix a connection, resume the feature we came from.
    if (!returnToFeature()) clearSettingsAction('connections')
  }

  const handleEditClose = () => {
    setEditingTarget(null)
    // Connection failures can route here from another feature. Closing the
    // repair should be symmetrical with saving it: return to the owning
    // feature when one was provided, otherwise clean the action deep-link.
    if (!returnToFeature()) clearSettingsAction('connections')
  }

  // Confirmation is handled by the styled ConfirmDialog inside the list.
  const handleDelete = async (targetName: string) => {
    await removeTarget(targetName)
    // The sidebar and Home drop the removed database without a navigation.
    notifyDatabasesChanged()
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

  const passwordDialogRequirements = useMemo(
    () =>
      (envRequirements?.requirements ?? []).filter(
        (requirement) =>
          requirement.kind === 'target_password' &&
          requirement.target === passwordDialogTarget
      ),
    [envRequirements, passwordDialogTarget]
  )

  const readings = monitorSettings.data
  const preferencesQuery = usePreferences()
  const updatePreferences = useUpdatePreferences()
  const preferences = preferencesQuery.data ?? PREFERENCE_DEFAULTS
  const preferencesBusy = !preferencesQuery.data || updatePreferences.isPending

  return (
    <>
      <PageBar
        title="Settings"
        meta={<span>{thisComputer()}</span>}
        actions={
          currentTarget ? (
            <Link
              to="/database"
              search={{ panel: 'settings' } as never}
              className={ghostLink}
            >
              Monitoring settings for {currentTarget}
            </Link>
          ) : undefined
        }
      />
      <PageContainer className="pt-9 pb-24">
        <SettingsGroup id="account" title="Account">
          <SettingsRow
            title={email ?? 'Readyset account'}
            line={email ? 'Signed in' : 'Not signed in'}
          >
            {email ? (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Sign out"
                loading={accountLogoutMutation.isPending}
                onClick={() => accountLogoutMutation.mutate()}
              />
            ) : (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Sign in"
                onClick={() => void navigate({ to: '/account-login' })}
              />
            )}
          </SettingsRow>
        </SettingsGroup>

        <SettingsGroup id="ai" title="AI">
          <SettingsRow
            title="Hosted by Readyset"
            line={
              readysetAccountConnected
                ? 'Analyze, Ask, and Health check use your Readyset account.'
                : usingReadyset
                  ? 'Sign in again to use the included AI.'
                  : undefined
            }
          >
            <BaseInputSwitch
              name="ai-hosted"
              aria-label="Hosted by Readyset"
              checked={usingReadyset}
              disabled={aiProviderMutation.isPending}
              onCheckedChange={handleHostedChange}
            />
          </SettingsRow>
          <SettingsRow
            title="Use my own key"
            line="Your provider bills you directly."
          >
            <Show when={hasAnthropicKey}>
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Test key"
                loading={keyValidityQuery.isFetching}
                onClick={() => void keyValidityQuery.refetch()}
              />
            </Show>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label={anthropicKeyConfigured ? 'Update key' : 'Add key'}
              onClick={() => setShowAnthropicDialog(true)}
            />
          </SettingsRow>
          <Show
            when={
              Boolean(keyValidity) &&
              hasAnthropicKey &&
              !keyValidityQuery.isFetching
            }
          >
            {keyValidity ? (
              <div className="mt-3">
                <Alert
                  tone="quiet"
                  variant={keyValidationVariant(keyValidity)}
                  label={keyValidationLabel(keyValidity)}
                />
              </div>
            ) : null}
          </Show>
          <div className="mt-3">
            <AiKeyRouting
              provider={activeAiProvider}
              signedIn={Boolean(readysetAccountAvailable)}
            />
          </div>
        </SettingsGroup>

        <SettingsGroup id="notifications" title="Notifications">
          <SettingsRow
            title={`Push to ${thisComputer()}`}
            line="Act now items and the daily report."
          >
            <BaseInputSwitch
              name="push"
              aria-label={`Push to ${thisComputer()}`}
              checked={pushOn}
              onCheckedChange={(on) => void setDesktopNotifications(on)}
            />
          </SettingsRow>
          <Text as="p" level="caption" className="mt-1.5 text-content-layout-3">
            Per-item choices are in the{' '}
            <Link to="/inbox" className={quietLink}>
              Inbox
            </Link>
            .
          </Text>
        </SettingsGroup>

        <SettingsGroup title="Appearance">
          {/* RDST draws dark only; a stored light, system or font size choice is kept but
              not offered until it applies. */}
          <SettingsRow title="Theme" line="Light colors are coming.">
            <Text
              as="span"
              level="body-small"
              className="text-content-layout-2"
            >
              Dark
            </Text>
          </SettingsRow>
        </SettingsGroup>

        <SettingsGroup id="privacy" title="Data">
          <SettingsRow
            title="Keep readings"
            line={
              readings
                ? `${formatBytes(readings.size_bytes)} of ${formatBytes(readings.budget_bytes)} used.`
                : undefined
            }
          >
            <PreferenceMenu
              label="Keep readings"
              value={preferences.keep_readings_days}
              options={KEEP_READINGS}
              optionLabel={keepReadingsLabel}
              disabled={preferencesBusy}
              onChange={(keep_readings_days) =>
                updatePreferences.mutate({ keep_readings_days })
              }
            />
          </SettingsRow>
          <SettingsRow
            title="Local data"
            line={`Readings, reports and analyses on ${thisComputer()}.`}
          >
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Clear local data"
              disabled={resetMutation.isPending || !dataDirectory}
              onClick={() => setConfirmClear(true)}
            />
          </SettingsRow>
          {statusQuery.isError && (
            <div className="mt-3">
              <ErrorState
                tone="quiet"
                errorClass={classifyError({
                  code: '',
                  message: 'Local storage details could not be loaded.',
                })}
                title="Local storage details couldn't be loaded"
                message="RDST couldn't read its local data directory."
                trustworthy="No local data was changed."
                onRetry={() => void statusQuery.refetch()}
              />
            </div>
          )}
        </SettingsGroup>

        <SettingsGroup
          id="connections"
          title="Databases"
          action={
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Add a database"
              onClick={() => openAddDatabase()}
            />
          }
        >
          <Show when={!!error}>
            <div className="mb-3">
              <ErrorState
                tone="quiet"
                errorClass={classifyError({
                  code: '',
                  message: error ?? '',
                })}
                title="Database action failed"
                message="The last action on your databases could not complete."
                trustworthy="Your saved databases are unchanged."
                detail={error ?? undefined}
                onRetry={() => void listTargets()}
              />
            </div>
          </Show>
          <Show when={!!connectivityError}>
            <div className="mb-3">
              <ErrorState
                tone="quiet"
                errorClass={classifyError({
                  code: '',
                  message: connectivityError ?? '',
                })}
                title="Connectivity check failed"
                message="Readyset could not check which of your databases are reachable right now."
                trustworthy="The database list is unaffected - only the live reachability of each row is stale."
                detail={connectivityError ?? undefined}
                onRetry={checkAll}
              />
            </div>
          </Show>
          <DatabaseRows
            targets={targets}
            pending={targetsPending}
            connectivity={connectivity}
            checkable={checkableTargets}
            testingName={testingTarget}
            onTest={(name) => void handleTest(name)}
            onEdit={(name) => void handleEditClick(name)}
            onSetDefault={(name) => void handleSetDefault(name)}
            onRemove={(name) => void handleDelete(name)}
            onSetPassword={(target) => {
              // A target added since the last read has no requirement in the cached list yet.
              void queryClient.invalidateQueries({
                queryKey: ['env-requirements'],
              })
              setPasswordDialogTarget(target.name)
            }}
            onTestTunnel={(name) => void handleTunnelTest(name)}
          />
          <AddDatabaseDialog
            request={null}
            edit={editingTarget}
            onClose={handleEditClose}
            onSaved={() => void handleEditSaved()}
          />
          <TargetsSlotsElsewhere />
        </SettingsGroup>

        <SettingsGroup title="About">
          <SettingsRow
            title="Version"
            line={
              statusData?.version ? (
                <span className="tabular-nums">{statusData.version}</span>
              ) : undefined
            }
          >
            <DesktopUpdateControl
              state={updates.state}
              install={updates.install}
            />
          </SettingsRow>
          <div className="flex gap-5 py-3.5 text-body-small text-content-layout-2">
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noreferrer"
              className={quietLink}
            >
              Docs
            </a>
            <Pressable
              className={`cursor-pointer ${quietLink}`}
              onClick={() => setFeedbackOpen(true)}
            >
              Give feedback
            </Pressable>
            <a
              href={DISCORD_INVITE_URL}
              target="_blank"
              rel="noreferrer"
              className={quietLink}
            >
              Discord
            </a>
          </div>
        </SettingsGroup>

        {import.meta.env.DEV ? (
          <SettingsGroup id="dev" title="Developer settings">
            <ClearKeyringRow />
          </SettingsGroup>
        ) : null}
      </PageContainer>

      <ConfirmDialog
        tone="quiet"
        isOpen={confirmClear}
        onClose={() => setConfirmClear(false)}
        onConfirm={() => resetMutation.mutate()}
        title="Clear local data?"
        subtitle={dataDirectory ?? undefined}
        confirmLabel="Clear local data"
        confirmVariant="negative"
        loading={resetMutation.isPending}
        blockCloseWhileLoading
        notice={{
          accent: 'negative',
          title: "This can't be undone",
          message:
            "This deletes RDST's local configuration and history. It also deletes schema descriptions and stored RDST keys. Your ~/.ssh directory is unchanged.",
        }}
      >
        {resetMutation.error instanceof Error ? (
          <Text level="body-small" className="text-content-negative-soft">
            {resetMutation.error.message}
          </Text>
        ) : null}
      </ConfirmDialog>

      <ReportDialog
        isOpen={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
      />

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

      <EnvSecretsDialog
        isOpen={showAnthropicDialog}
        onClose={() => {
          setShowAnthropicDialog(false)
          if (!returnToFeature()) clearSettingsAction('ai')
        }}
        requirements={anthropicDialogRequirements}
        showManualAnthropicInput
        keyringAvailable={Boolean(envRequirements?.keyring_available)}
        onTrialRegister={() => setShowTrialDialog(true)}
        trialActionLabel="Don't have a key? Sign in to Readyset"
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          // Re-check right away so the status line reflects the new key even
          // while the requirements refresh is still in flight.
          void keyValidityQuery.refetch()
          // If a routable "needs a key" notice sent us here, resume the feature.
          if (!returnToFeature()) clearSettingsAction('ai')
        }}
      />

      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => {
          setShowTrialDialog(false)
          if (!returnToFeature()) clearSettingsAction('ai')
        }}
        onSuccess={() => {
          aiProviderMutation.mutate('readyset')
          if (!returnToFeature()) clearSettingsAction('ai')
        }}
      />
    </>
  )
}

// Slots held for databases that aren't on this computer, so Manage databases can free them.
function TargetsSlotsElsewhere() {
  const limit = useMonitoringLimit()
  return <SlotsElsewhere limit={limit.data} />
}
