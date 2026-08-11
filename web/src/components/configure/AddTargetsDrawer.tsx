/**
 * Bulk-add drawer for the Settings target list: discover AWS RDS/Aurora
 * instances, discover Supabase, Neon or DigitalOcean databases, or import a
 * CSV, then set credentials for whatever landed.
 *
 * Discovery is preview-first — everything found is listed, the user picks, and
 * only the picked instances are added. Passwords never travel in the CSV; the
 * credentials step right after the import is where they get set.
 */

import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import {
  Drawer,
  DrawerContent,
  DrawerContentContainer,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@rs/ui-new/drawer'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type ComponentType,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { fetchEnvRequirements } from '../../lib/api'
import {
  bulkAddFleetTargets,
  type DiscoveredFleetMember,
  type FleetDiscoverInput,
  fetchFleetAwsStatus,
  fetchFleetDigitaloceanStatus,
  fetchFleetDiscoverPreview,
  fetchFleetNeonStatus,
  fetchFleetSupabaseStatus,
  fetchFleetTargets,
  useFleetImport,
} from '../../lib/useFleet'
import type {
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
} from '../../types/fleet'
import { AwsConnectionPanel } from '../aws/AwsConnectionPanel'
import { DigitalOceanConnectionPanel } from '../digitalocean/DigitalOceanConnectionPanel'
import { NeonConnectionPanel } from '../neon/NeonConnectionPanel'
import {
  AwsLogo,
  AzureLogo,
  DigitalOceanLogo,
  GcpLogo,
  NeonLogo,
  SupabaseLogo,
} from '../providers/ProviderLogos'
import { SupabaseConnectionPanel } from '../supabase/SupabaseConnectionPanel'
import { ADD_TABS, type AddTab } from './addTabs'
import { CredentialsStep } from './CredentialsStep'
import { groupPrivateTargets, type PrivateTargetGroup } from './privateTargets'
import { groupTargets } from './TargetGroupView'

// The regions most RDS fleets live in, offered as one-click toggles; picking
// another region from the dropdown promotes it into the same toggle bar.
const PRESET_REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2']
const OTHER_REGIONS = [
  'af-south-1',
  'ap-east-1',
  'ap-northeast-1',
  'ap-northeast-2',
  'ap-northeast-3',
  'ap-south-1',
  'ap-south-2',
  'ap-southeast-1',
  'ap-southeast-2',
  'ap-southeast-3',
  'ap-southeast-4',
  'ca-central-1',
  'eu-central-1',
  'eu-central-2',
  'eu-north-1',
  'eu-south-1',
  'eu-south-2',
  'eu-west-1',
  'eu-west-2',
  'eu-west-3',
  'me-central-1',
  'me-south-1',
  'sa-east-1',
]

// Proper engine display names - the backend emits lowercase enum values, which
// CSS `capitalize` renders as "Postgresql"/"Mysql".
const ENGINE_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}
const engineLabel = (engine: string | null | undefined) =>
  engine ? (ENGINE_LABELS[engine.toLowerCase()] ?? engine) : '-'

/** "role:writer" -> "writer"; other tags return null. */
const roleOfTag = (tag: string): string | null =>
  tag.startsWith('role:') ? tag.slice('role:'.length) : null

/** The account providers, whose discovery reads one connected account. */
type AccountTab = Exclude<AddTab, 'aws' | 'csv'>

interface ProviderTab {
  label: string
  description: string
  Logo: (props: { size?: number }) => ReactNode
  /** Shown when discovery came back with nothing. */
  emptyMessage: string
  /** Absent for AWS, whose discovery is driven by a region form instead. */
  account?: {
    fetchStatus: () => Promise<{ connected: boolean }>
    Panel: ComponentType
  }
}

const PROVIDERS: Record<Exclude<AddTab, 'csv'>, ProviderTab> = {
  aws: {
    label: 'AWS',
    description: 'Import RDS and Aurora databases',
    Logo: AwsLogo,
    emptyMessage: 'No databases found in the selected regions.',
  },
  supabase: {
    label: 'Supabase',
    description: 'Discover projects from your account',
    Logo: SupabaseLogo,
    emptyMessage: 'No projects found in your Supabase organizations.',
    account: {
      fetchStatus: fetchFleetSupabaseStatus,
      Panel: SupabaseConnectionPanel,
    },
  },
  neon: {
    label: 'Neon',
    description: 'Import databases with a Neon API key',
    Logo: NeonLogo,
    emptyMessage: 'No projects found in your Neon account.',
    account: { fetchStatus: fetchFleetNeonStatus, Panel: NeonConnectionPanel },
  },
  digitalocean: {
    label: 'DigitalOcean',
    description: 'Import managed PostgreSQL and MySQL',
    Logo: DigitalOceanLogo,
    emptyMessage: 'No databases found in your DigitalOcean account.',
    account: {
      fetchStatus: fetchFleetDigitaloceanStatus,
      Panel: DigitalOceanConnectionPanel,
    },
  },
}

const CLOUD_TABS = ADD_TABS.filter(
  (tab): tab is Exclude<AddTab, 'csv'> => tab !== 'csv'
)

// Providers on the roadmap, shown as disabled tiles so the picker previews
// what is coming without offering a dead click.
const COMING_SOON_PROVIDERS = [
  { label: 'Azure', description: 'Import Azure databases', Logo: AzureLogo },
  { label: 'GCP', description: 'Import Cloud SQL databases', Logo: GcpLogo },
]

type ConnectionSetupMode = 'integrations' | 'manual'
type IntegrationView = 'picker' | 'provider'

function StreamLog({
  progress,
  errors,
  result,
  onClear,
}: {
  progress: FleetImportProgressEvent[]
  errors: string[]
  result: FleetImportCompleteEvent | undefined
  onClear: () => void
}) {
  const hasContent = progress.length > 0 || errors.length > 0 || !!result

  return (
    <AnimatePresence>
      {hasContent && (
        <m.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="overflow-hidden"
        >
          <div className="bg-surface-layout-2/50 rounded-lg border border-border-layout-1">
            <Scrollable className="max-h-64">
              <VStack className="gap-1 items-stretch p-4">
                {progress.map((entry, index) => (
                  <HStack key={index} className="gap-2 items-center">
                    <Tag
                      size="small"
                      variant={
                        entry.status === 'skipped' ? 'warning' : 'positive'
                      }
                      modifier="ghost"
                      label={entry.status}
                    />
                    <Text level="caption" className="text-content-layout-2">
                      {entry.message}
                    </Text>
                  </HStack>
                ))}
                {errors.map((message, index) => (
                  <HStack key={`err-${index}`} className="gap-2 items-start">
                    <Icon
                      name="alert"
                      label="Error"
                      className="w-3.5 h-3.5 text-content-negative-soft mt-0.5 shrink-0"
                    />
                    <Text
                      level="caption"
                      className="text-content-negative-soft"
                    >
                      {message}
                    </Text>
                  </HStack>
                ))}
                {result && (
                  <HStack className="gap-2 items-center pt-2">
                    <Icon
                      name={result.success ? 'tick-double' : 'alert'}
                      label="Result"
                      className={`w-4 h-4 ${result.success ? 'text-content-positive-soft' : 'text-content-negative-soft'}`}
                    />
                    <Text level="label-small" className="text-content-layout-1">
                      {result.imported} imported, {result.skipped} skipped,{' '}
                      {result.errors} errors
                    </Text>
                  </HStack>
                )}
                {(result || errors.length > 0) && (
                  <HStack className="justify-end pt-1">
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label="Clear"
                      onClick={onClear}
                    />
                  </HStack>
                )}
              </VStack>
            </Scrollable>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  )
}

function ConnectionSourceCard({
  label,
  description,
  logo,
  onClick,
  comingSoon = false,
}: {
  label: string
  description: string
  logo: ReactNode
  onClick?: () => void
  comingSoon?: boolean
}) {
  return (
    <Pressable
      type="button"
      onClick={onClick}
      disabled={comingSoon}
      aria-label={comingSoon ? `${label}, coming soon` : label}
      className="group flex min-h-24 w-full items-start gap-3 rounded-xl border border-border-layout-1 bg-surface-layout-2/35 p-4 text-left transition-colors hover:bg-surface-rising-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft disabled:cursor-default disabled:border-dashed disabled:opacity-55 disabled:hover:bg-surface-layout-2/35"
    >
      <span
        className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-surface-layout-2 text-content-layout-1"
        aria-hidden="true"
      >
        {logo}
      </span>
      <VStack className="min-w-0 flex-1 gap-1 items-start">
        <HStack className="w-full gap-2 items-center">
          <Text level="label-small" className="text-content-layout-1">
            {label}
          </Text>
          {comingSoon ? (
            <Tag
              size="small"
              variant="neutral"
              modifier="ghost"
              label="Soon"
              className="ml-auto"
            />
          ) : (
            <Icon
              name="arrow-right"
              label=""
              aria-hidden="true"
              className="ml-auto size-4 text-content-layout-3 transition-transform group-hover:translate-x-0.5"
            />
          )}
        </HStack>
        <Text level="caption" className="text-content-layout-3">
          {description}
        </Text>
      </VStack>
    </Pressable>
  )
}

export interface AddTargetsDrawerProps {
  open: boolean
  /** Which source the drawer deep-links into. Without one, show the picker. */
  initialTab?: AddTab
  /** The existing manual connection form, owned by the settings controller. */
  manualContent?: ReactNode
  onClose: () => void
  /** Targets landed in the config: refresh the page's target rows. */
  onTargetsAdded: () => void
  /** The credentials step was left: the page's connectivity results are stale. */
  onCredentialsClosed: () => void
  /** Re-run the page's own connectivity check for exactly these targets. */
  onRecheckTargets?: (names: string[]) => void
}

export function AddTargetsDrawer({
  open,
  initialTab,
  manualContent,
  onClose,
  onTargetsAdded,
  onCredentialsClosed,
  onRecheckTargets,
}: AddTargetsDrawerProps) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<'source' | 'credentials'>('source')
  const [setupMode, setSetupMode] =
    useState<ConnectionSetupMode>('integrations')
  const [integrationView, setIntegrationView] = useState<IntegrationView>(
    initialTab ? 'provider' : 'picker'
  )
  const [addTab, setAddTab] = useState<AddTab>(initialTab ?? 'aws')
  const [credentialTargetNames, setCredentialTargetNames] = useState<string[]>(
    []
  )
  const [privateTargetGroups, setPrivateTargetGroups] = useState<
    PrivateTargetGroup[]
  >([])

  // Each opening starts on the requested source tab, never on a leftover step.
  useEffect(() => {
    if (!open) return
    setStep('source')
    setSetupMode('integrations')
    setIntegrationView(initialTab ? 'provider' : 'picker')
    setAddTab(initialTab ?? 'aws')
    setPrivateTargetGroups([])
  }, [open, initialTab])

  const { data: fleetTargets } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
    enabled: open,
  })
  const { data: envRequirements } = useQuery({
    queryKey: ['env-requirements'],
    queryFn: fetchEnvRequirements,
    staleTime: 5_000,
    enabled: open,
  })
  // Supabase, Neon and DigitalOcean each discover from one connected account:
  // no region form, and a single Discover button armed by that provider's
  // connection.
  const accountTab: AccountTab | undefined =
    addTab !== 'csv' && PROVIDERS[addTab].account
      ? (addTab as AccountTab)
      : undefined
  // Shares its cache entry with that provider's connection panel, so signing in
  // there arms the Discover button here.
  const { data: accountStatus } = useQuery({
    queryKey: [`fleet-${accountTab}-status`],
    queryFn: () => PROVIDERS[accountTab as AccountTab].account?.fetchStatus(),
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    retry: false,
    enabled: open && !!accountTab,
  })
  const accountConnected = Boolean(accountStatus?.connected)
  const AccountPanel = accountTab && PROVIDERS[accountTab].account?.Panel

  // Import form. The CSV comes through the browser's file picker as raw text.
  const [csvUpload, setCsvUpload] = useState<{
    name: string
    content: string
  } | null>(null)
  const csvFileInputRef = useRef<HTMLInputElement | null>(null)
  const {
    runImport,
    state: importState,
    progress: importProgress,
    result: importResult,
    errors: importErrors,
    reset: resetImport,
  } = useFleetImport()
  const isImporting = importState === 'running'

  // AWS discover form. All common regions start selected; custom ones join the
  // chip bar as they're added.
  const [selectedRegions, setSelectedRegions] = useState<string[]>([
    ...PRESET_REGIONS,
  ])
  const [customRegions, setCustomRegions] = useState<string[]>([])
  const [discoverProfile, setDiscoverProfile] = useState('')
  const [previewMembers, setPreviewMembers] = useState<
    DiscoveredFleetMember[] | null
  >(null)
  const [previewErrors, setPreviewErrors] = useState<string[]>([])
  const [previewLoading, setPreviewLoading] = useState(false)
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set())
  const [previewRegionCount, setPreviewRegionCount] = useState(0)
  const [addingTargets, setAddingTargets] = useState(false)

  // Shares the AWS connection panel's cache entry (same key + profile), so
  // signing in or out there arms or disarms the Discover button here.
  const { data: awsStatus } = useQuery({
    queryKey: ['fleet-aws-status', discoverProfile],
    queryFn: () => fetchFleetAwsStatus(discoverProfile || undefined),
    staleTime: 5_000,
    retry: false,
    enabled: open && addTab === 'aws',
  })
  const awsConnected = Boolean(awsStatus?.has_credentials)
  // The provider driving the active tab's discovery: AWS by its credentials,
  // the account providers by their connection, and CSV never gates.
  const activeConnected =
    addTab === 'aws' ? awsConnected : accountTab ? accountConnected : true

  const toggleRegion = (region: string) =>
    setSelectedRegions((current) =>
      current.includes(region)
        ? current.filter((r) => r !== region)
        : [...current, region]
    )
  // Picking a region from the dropdown promotes it into the toggle bar,
  // pre-selected; from there it toggles like the presets.
  const promoteRegion = (region: string) => {
    if (!region) return
    setCustomRegions((current) =>
      current.includes(region) ? current : [...current, region]
    )
    setSelectedRegions((current) =>
      current.includes(region) ? current : [...current, region]
    )
  }
  const selectRegion = (region: string) =>
    setSelectedRegions((current) =>
      current.includes(region) ? current : [...current, region]
    )

  const previewGroups = useMemo(
    () => groupTargets(previewMembers ?? [], (member) => member.group),
    [previewMembers]
  )
  const selectablePreviewNames = (previewMembers ?? [])
    .filter((member) => !member.already_exists)
    .map((member) => member.name)
  const allPreviewSelected =
    selectablePreviewNames.length > 0 &&
    selectablePreviewNames.every((name) => selectedNames.has(name))

  const selectedNewCount = previewMembers
    ? previewMembers.filter(
        (member) => selectedNames.has(member.name) && !member.already_exists
      ).length
    : 0

  // Newly-added targets must reach every consumer at once: the fleet list this
  // drawer reads, the page's own target rows, and the global no-targets lockout.
  const publishNewTargets = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] }),
      queryClient.invalidateQueries({ queryKey: ['status'] }),
      queryClient.invalidateQueries({ queryKey: ['init-status'] }),
    ])
    onTargetsAdded()
  }

  // Saved passwords change what the page's rows say about themselves, and the
  // page reads its targets and its connectivity from state this drawer does not
  // own: republish the list and re-check exactly the targets that were saved.
  const publishSavedCredentials = async (names: string[]) => {
    await publishNewTargets()
    onRecheckTargets?.(names)
  }

  const handleCsvFile = async (file: File | undefined) => {
    if (!file) return
    resetImport()
    setCsvUpload({ name: file.name, content: await file.text() })
  }

  const handleImport = async () => {
    if (!csvUpload) return
    const completion = await runImport({ csv_content: csvUpload.content })
    if (completion && completion.imported > 0) {
      await publishNewTargets()
      setCredentialTargetNames(completion.target_names)
      setPrivateTargetGroups([])
      setStep('credentials')
    }
  }

  const handleDiscoverPreview = async (input: FleetDiscoverInput) => {
    setPreviewLoading(true)
    setPreviewErrors([])
    try {
      const preview = await fetchFleetDiscoverPreview(input)
      setPreviewRegionCount('regions' in input ? input.regions.length : 0)
      setPreviewMembers(preview.members)
      setPreviewErrors(preview.errors)
      setSelectedNames(
        new Set(
          preview.members
            .filter((member) => !member.already_exists)
            .map((member) => member.name)
        )
      )
    } catch (caught) {
      setPreviewErrors([
        caught instanceof Error ? caught.message : String(caught),
      ])
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleAddSelected = async () => {
    if (!previewMembers) return
    const chosen = previewMembers.filter(
      (member) => selectedNames.has(member.name) && !member.already_exists
    )
    if (chosen.length === 0) return
    setAddingTargets(true)
    try {
      const added = await bulkAddFleetTargets(chosen)
      await publishNewTargets()
      setPreviewMembers(null)
      if (added.target_names.length > 0) {
        setCredentialTargetNames(added.target_names)
        const addedNames = new Set(added.target_names)
        setPrivateTargetGroups(
          groupPrivateTargets(chosen)
            .map((group) => ({
              ...group,
              targetNames: group.targetNames.filter((name) =>
                addedNames.has(name)
              ),
            }))
            .filter((group) => group.targetNames.length > 0)
        )
        setStep('credentials')
      }
    } catch (caught) {
      setPreviewErrors([
        caught instanceof Error ? caught.message : String(caught),
      ])
    } finally {
      setAddingTargets(false)
    }
  }

  // A preview belongs to the provider that produced it, so switching source
  // clears it rather than showing another provider's databases.
  const selectTab = (tab: AddTab) => {
    setAddTab(tab)
    setIntegrationView('provider')
    setPreviewMembers(null)
    setPreviewErrors([])
    setPreviewRegionCount(0)
  }

  const selectSetupMode = (mode: ConnectionSetupMode) => {
    setSetupMode(mode)
    setPreviewMembers(null)
    setSelectedNames(new Set())
    setPreviewErrors([])
    setPreviewRegionCount(0)
  }

  const showIntegrationPicker = () => {
    setIntegrationView('picker')
    setPreviewMembers(null)
    setSelectedNames(new Set())
    setPreviewErrors([])
    setPreviewRegionCount(0)
  }

  // Signing out of the active provider retires whatever it discovered: the
  // listed databases can no longer be added, so drop the preview and selection
  // the instant its connection flips off.
  const wasConnected = useRef(activeConnected)
  useEffect(() => {
    if (wasConnected.current && !activeConnected) {
      setPreviewMembers(null)
      setSelectedNames(new Set())
      setPreviewErrors([])
    }
    wasConnected.current = activeConnected
  }, [activeConnected])

  // A preview belongs to the AWS profile it was discovered under. Switching
  // profiles (accounts) must drop it -- otherwise an empty result from one
  // account keeps showing over the Discover form and blocks re-discovering
  // under the next.
  const previewProfile = useRef(discoverProfile)
  useEffect(() => {
    if (previewProfile.current !== discoverProfile) {
      previewProfile.current = discoverProfile
      setPreviewMembers(null)
      setSelectedNames(new Set())
      setPreviewErrors([])
    }
  }, [discoverProfile])

  const closeDrawer = () => {
    const leftCredentials = step === 'credentials'
    setStep('source')
    setSetupMode('integrations')
    setIntegrationView(initialTab ? 'provider' : 'picker')
    setCredentialTargetNames([])
    setPrivateTargetGroups([])
    setPreviewMembers(null)
    setPreviewErrors([])
    onClose()
    // Freshly saved credentials should reflect in the list immediately, rather
    // than leaving the rows unreachable from before the passwords existed:
    // republish the rows, and let onCredentialsClosed re-sweep connectivity.
    if (leftCredentials) {
      void publishNewTargets()
      onCredentialsClosed()
    }
  }

  const credentialTargets = (fleetTargets?.members ?? []).filter((member) =>
    credentialTargetNames.includes(member.name)
  )

  return (
    <Drawer
      open={open}
      onOpenChange={(next) => (next ? undefined : closeDrawer())}
      direction="right"
    >
      <DrawerContentContainer>
        {open && (
          <DrawerContent size="XLarge" direction="right" className="p-0">
            <DrawerHeader className="p-5 border-b border-border-layout-1">
              <DrawerTitle>
                {step === 'credentials' ? 'Set credentials' : 'Add connection'}
              </DrawerTitle>
              <DrawerDescription>
                {step === 'credentials'
                  ? 'Enter credentials and test the imported databases.'
                  : 'Connect manually or import databases from a supported provider.'}
              </DrawerDescription>
              {step === 'source' && (
                <SegmentedControl
                  aria-label="Connection setup method"
                  className="mt-4 w-full"
                  mode="tabs"
                  panelId="connection-setup-panel"
                  value={setupMode}
                  segments={[
                    {
                      value: 'integrations',
                      label: 'Integrations',
                      icon: 'connect',
                    },
                    {
                      value: 'manual',
                      label: 'Manual setup',
                      icon: 'edit',
                    },
                  ]}
                  onValueChange={selectSetupMode}
                />
              )}
            </DrawerHeader>

            <div id="connection-setup-panel" className="flex min-h-0 flex-1">
              <Scrollable className="flex-1 p-5">
                {step === 'credentials' ? (
                  <CredentialsStep
                    targets={credentialTargets}
                    keyringAvailable={
                      envRequirements?.keyring_available ?? false
                    }
                    onClose={closeDrawer}
                    onSaved={publishSavedCredentials}
                    privateTargetGroups={privateTargetGroups}
                  />
                ) : setupMode === 'manual' ? (
                  (manualContent ?? (
                    <InlineNotice
                      errorClass="user-config"
                      title="Manual setup is unavailable"
                      message="Close this drawer and try again."
                      trustworthy="Your saved connections are unchanged."
                    />
                  ))
                ) : integrationView === 'picker' ? (
                  <VStack className="gap-5 items-stretch">
                    <VStack className="gap-1 items-start">
                      <Text
                        level="label-medium"
                        className="text-content-layout-1"
                      >
                        Choose an integration
                      </Text>
                      <Text
                        level="body-small"
                        className="text-content-layout-3"
                      >
                        Import databases from an account or a prepared CSV. You
                        can review everything before it is added.
                      </Text>
                    </VStack>
                    <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
                      {CLOUD_TABS.map((tab) => {
                        const { label, description, Logo } = PROVIDERS[tab]
                        return (
                          <ConnectionSourceCard
                            key={tab}
                            label={label}
                            description={description}
                            logo={<Logo size={20} />}
                            onClick={() => selectTab(tab)}
                          />
                        )
                      })}
                      <ConnectionSourceCard
                        label="CSV file"
                        description="Import a prepared list of database connections"
                        logo={
                          <Icon
                            name="folder-file"
                            label=""
                            aria-hidden="true"
                            className="size-5"
                          />
                        }
                        onClick={() => selectTab('csv')}
                      />
                      {COMING_SOON_PROVIDERS.map(
                        ({ label, description, Logo }) => (
                          <ConnectionSourceCard
                            key={label}
                            comingSoon
                            label={label}
                            description={description}
                            logo={<Logo size={20} />}
                          />
                        )
                      )}
                    </div>
                    <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 p-4">
                      <HStack className="gap-3 items-start">
                        <Icon
                          name="user-shield"
                          label=""
                          aria-hidden="true"
                          className="mt-0.5 size-4 shrink-0 text-content-layout-3"
                        />
                        <Text level="caption" className="text-content-layout-3">
                          Discovery is preview-first. RDST never imports a
                          database until you select it, and passwords stay in
                          your local secret store.
                        </Text>
                      </HStack>
                    </div>
                  </VStack>
                ) : (
                  <>
                    <HStack className="mb-5 gap-3 items-center">
                      <Button
                        variant="primary"
                        modifier="ghost"
                        size="small"
                        icon="arrow-left"
                        iconPosition="left"
                        label="All integrations"
                        onClick={showIntegrationPicker}
                      />
                      <div className="h-4 w-px bg-border-layout-1" />
                      <HStack className="gap-2 items-center">
                        {addTab === 'csv' ? (
                          <Icon
                            name="folder-file"
                            label=""
                            aria-hidden="true"
                            className="size-4 text-content-layout-2"
                          />
                        ) : (
                          (() => {
                            const Logo = PROVIDERS[addTab].Logo
                            return <Logo size={16} />
                          })()
                        )}
                        <Text
                          level="label-small"
                          className="text-content-layout-1"
                        >
                          {addTab === 'csv'
                            ? 'CSV file'
                            : PROVIDERS[addTab].label}
                        </Text>
                      </HStack>
                    </HStack>
                    <Show when={addTab === 'csv'}>
                      <VStack className="gap-4 items-stretch">
                        <Text
                          level="body-small"
                          className="text-content-layout-3"
                        >
                          Choose a CSV to add database targets.
                        </Text>
                        <div className="rounded-lg bg-surface-layout-2/60 border border-border-layout-1 px-4 py-3">
                          <Text
                            level="caption"
                            className="text-content-layout-3 mb-2 block"
                          >
                            Example — columns: name, host, engine (plus optional
                            port, database, user)
                          </Text>
                          <pre className="text-xs text-content-layout-2 overflow-x-auto leading-relaxed">
                            {'name,host,port,database,user,engine\n' +
                              'prod-orders,orders.abc1.us-east-1.rds.amazonaws.com,5432,orders,app_ro,postgresql\n' +
                              'analytics,analytics.internal,3306,metrics,readonly,mysql'}
                          </pre>
                        </div>
                        <HStack className="gap-3 items-center">
                          <input
                            type="file"
                            accept=".csv,text/csv"
                            ref={csvFileInputRef}
                            className="hidden"
                            onChange={(e) =>
                              void handleCsvFile(e.target.files?.[0])
                            }
                          />
                          <Button
                            variant="primary"
                            modifier="outline"
                            label={
                              csvUpload
                                ? 'Choose a different file'
                                : 'Choose CSV file'
                            }
                            icon="folder-file"
                            iconPosition="left"
                            disabled={isImporting}
                            onClick={() => csvFileInputRef.current?.click()}
                          />
                          {csvUpload && (
                            <Text
                              level="caption"
                              className="text-content-layout-2"
                            >
                              {csvUpload.name}
                            </Text>
                          )}
                        </HStack>

                        <HStack className="gap-3 items-center justify-end">
                          <Button
                            variant="primary"
                            modifier="solid"
                            label="Import"
                            icon="add"
                            iconPosition="left"
                            onClick={handleImport}
                            loading={isImporting}
                            disabled={!csvUpload || isImporting}
                          />
                        </HStack>

                        <StreamLog
                          progress={importProgress}
                          errors={importErrors}
                          result={importResult}
                          onClear={resetImport}
                        />
                      </VStack>
                    </Show>

                    {/* Every cloud provider shares everything below the
                      connection panel: only the discover form differs. */}
                    <Show when={addTab !== 'csv'}>
                      <VStack className="gap-4 items-stretch">
                        {AccountPanel ? (
                          <AccountPanel />
                        ) : (
                          <AwsConnectionPanel
                            profile={discoverProfile}
                            onProfileChange={setDiscoverProfile}
                            onRegionPrefill={(region) => {
                              if (!PRESET_REGIONS.includes(region)) {
                                setCustomRegions((current) =>
                                  current.includes(region)
                                    ? current
                                    : [...current, region]
                                )
                              }
                              selectRegion(region)
                            }}
                          />
                        )}
                        <Show
                          when={
                            addTab === 'aws' ||
                            addTab === 'digitalocean' ||
                            addTab === 'neon'
                          }
                        >
                          <Text
                            level="caption"
                            className="text-content-layout-3"
                          >
                            For a private database, import it first, then add an
                            SSH jump host in its connection details.
                          </Text>
                        </Show>
                        {previewMembers === null &&
                          (accountTab ? (
                            <HStack className="gap-3 items-center justify-end">
                              <Button
                                variant="primary"
                                modifier="solid"
                                label={
                                  previewLoading ? 'Discovering…' : 'Discover'
                                }
                                icon="search"
                                iconPosition="left"
                                onClick={() =>
                                  void handleDiscoverPreview({
                                    provider: accountTab,
                                  })
                                }
                                disabled={!accountConnected || previewLoading}
                              />
                            </HStack>
                          ) : (
                            <>
                              <div>
                                <Text
                                  level="caption"
                                  className="text-content-layout-3 mb-1 block"
                                >
                                  Regions
                                </Text>
                                <HStack className="gap-1 flex-wrap">
                                  {[...PRESET_REGIONS, ...customRegions].map(
                                    (region) => (
                                      <Button
                                        key={region}
                                        type="button"
                                        label={region}
                                        modifier={
                                          selectedRegions.includes(region)
                                            ? 'ghost'
                                            : 'outline'
                                        }
                                        onClick={() => toggleRegion(region)}
                                        classMerge="h-10 rounded-lg whitespace-nowrap"
                                      />
                                    )
                                  )}
                                </HStack>
                                <div className="mt-2 max-w-72">
                                  <BaseInputSelect
                                    name="fleet-discover-add-region"
                                    placeholder="Add another region…"
                                    value=""
                                    onValueChange={promoteRegion}
                                    options={OTHER_REGIONS.filter(
                                      (region) =>
                                        !customRegions.includes(region)
                                    ).map((region) => ({
                                      value: region,
                                      label: region,
                                    }))}
                                  />
                                </div>
                              </div>
                              <VStack className="gap-1.5 items-end">
                                <Button
                                  variant="primary"
                                  modifier="solid"
                                  label={
                                    previewLoading ? 'Discovering…' : 'Discover'
                                  }
                                  icon="search"
                                  iconPosition="left"
                                  onClick={() =>
                                    void handleDiscoverPreview({
                                      regions: selectedRegions,
                                      profile: discoverProfile || undefined,
                                    })
                                  }
                                  disabled={
                                    selectedRegions.length === 0 ||
                                    previewLoading ||
                                    !awsConnected
                                  }
                                />
                                {!awsConnected && (
                                  <Text
                                    level="caption"
                                    className="text-content-layout-3"
                                  >
                                    Sign in with AWS to discover instances
                                  </Text>
                                )}
                              </VStack>
                            </>
                          ))}
                        {previewMembers !== null && (
                          <>
                            <Text
                              level="body-small"
                              className="text-content-layout-2"
                            >
                              {previewMembers.length === 0
                                ? PROVIDERS[addTab as Exclude<AddTab, 'csv'>]
                                    .emptyMessage
                                : 'Choose which databases to add to your fleet.'}
                            </Text>
                            {previewMembers.some(
                              (member) => !member.already_exists
                            ) && (
                              <HStack className="gap-3 items-center">
                                <Button
                                  variant="primary"
                                  modifier="ghost"
                                  size="small"
                                  label={
                                    allPreviewSelected
                                      ? 'Clear selection'
                                      : 'Select all'
                                  }
                                  onClick={() =>
                                    setSelectedNames(
                                      allPreviewSelected
                                        ? new Set()
                                        : new Set(selectablePreviewNames)
                                    )
                                  }
                                />
                              </HStack>
                            )}
                            <VStack className="gap-4 items-stretch">
                              {previewGroups.map(({ group, targets: rows }) => (
                                <VStack
                                  key={group || 'ungrouped'}
                                  className="gap-1.5 items-stretch"
                                >
                                  <HStack className="gap-2 items-center">
                                    <Icon
                                      name="database"
                                      label=""
                                      aria-hidden="true"
                                      className="w-4 h-4 text-content-layout-3"
                                    />
                                    <Text
                                      level="overline"
                                      className="text-content-layout-3 uppercase tracking-wider"
                                    >
                                      {group || 'Ungrouped'}
                                    </Text>
                                    <Text
                                      level="caption"
                                      className="text-content-layout-3"
                                    >
                                      {rows.length === 1
                                        ? '1 instance'
                                        : `${rows.length} instances`}
                                    </Text>
                                    {rows.some(
                                      (member) => !member.already_exists
                                    ) && (
                                      <HStack className="ml-auto gap-1 items-center">
                                        <Button
                                          variant="primary"
                                          modifier="ghost"
                                          size="small"
                                          label={
                                            rows
                                              .filter(
                                                (member) =>
                                                  !member.already_exists
                                              )
                                              .every((member) =>
                                                selectedNames.has(member.name)
                                              )
                                              ? 'Clear selection'
                                              : 'Select all'
                                          }
                                          onClick={() =>
                                            setSelectedNames((current) => {
                                              const updated = new Set(current)
                                              const selectableRows =
                                                rows.filter(
                                                  (member) =>
                                                    !member.already_exists
                                                )
                                              const allSelected =
                                                selectableRows.every((member) =>
                                                  current.has(member.name)
                                                )
                                              for (const member of selectableRows) {
                                                if (allSelected)
                                                  updated.delete(member.name)
                                                else updated.add(member.name)
                                              }
                                              return updated
                                            })
                                          }
                                        />
                                      </HStack>
                                    )}
                                  </HStack>
                                  {rows.map((member) => {
                                    const role = member.tags
                                      .map(roleOfTag)
                                      .find(Boolean)
                                    const body = (
                                      // The checkbox is nested inside, behind a
                                      // design-system component.
                                      // biome-ignore lint/a11y/noLabelWithoutControl: nested control
                                      <label
                                        key={member.name}
                                        className={`flex items-start gap-3 rounded-lg border border-border-layout-1 px-3 py-2 ${
                                          member.already_exists
                                            ? 'bg-surface-layout-2/20'
                                            : 'bg-surface-layout-2/40 cursor-pointer hover:bg-surface-layout-2/70'
                                        }`}
                                      >
                                        <span className="mt-0.5 flex items-center shrink-0">
                                          {member.already_exists ? (
                                            <Icon
                                              name="tick"
                                              label="Already imported"
                                              className="w-4 h-4 text-content-positive-soft"
                                            />
                                          ) : (
                                            <BaseInputCheckbox
                                              checked={selectedNames.has(
                                                member.name
                                              )}
                                              onCheckedChange={(next) =>
                                                setSelectedNames((current) => {
                                                  const updated = new Set(
                                                    current
                                                  )
                                                  if (next === true)
                                                    updated.add(member.name)
                                                  else
                                                    updated.delete(member.name)
                                                  return updated
                                                })
                                              }
                                              aria-label={`Select ${member.name}`}
                                            />
                                          )}
                                        </span>
                                        <VStack className="gap-0.5 items-start min-w-0 flex-1">
                                          <HStack className="gap-2 items-center flex-wrap">
                                            <Text
                                              level="label-small"
                                              className="text-content-layout-1"
                                            >
                                              {member.name}
                                            </Text>
                                            <Tag
                                              size="small"
                                              variant="neutral"
                                              modifier="ghost"
                                              label={engineLabel(member.engine)}
                                            />
                                            {role && (
                                              <Tag
                                                size="small"
                                                variant="informative"
                                                modifier="ghost"
                                                label={role}
                                              />
                                            )}
                                            {previewRegionCount > 1 &&
                                              member.region && (
                                                <Tag
                                                  size="small"
                                                  variant="neutral"
                                                  modifier="ghost"
                                                  label={member.region}
                                                />
                                              )}
                                            {member.already_exists && (
                                              <Tag
                                                size="small"
                                                variant="positive"
                                                modifier="ghost"
                                                label="already imported"
                                              />
                                            )}
                                          </HStack>
                                          <Text
                                            level="caption"
                                            className="text-content-layout-3 truncate max-w-full"
                                          >
                                            {member.host}:{member.port} ·{' '}
                                            {member.database}
                                          </Text>
                                        </VStack>
                                      </label>
                                    )
                                    if (!member.already_exists) return body
                                    return (
                                      <TooltipProvider key={member.name}>
                                        <Tooltip>
                                          <TooltipTrigger asChild>
                                            {body}
                                          </TooltipTrigger>
                                          <TooltipContent label="This database has already been imported as a target." />
                                        </Tooltip>
                                      </TooltipProvider>
                                    )
                                  })}
                                </VStack>
                              ))}
                            </VStack>
                            <HStack className="gap-3 items-center justify-between">
                              <Button
                                variant="primary"
                                modifier="ghost"
                                size="small"
                                label={accountTab ? 'Back' : 'Back to regions'}
                                onClick={() => setPreviewMembers(null)}
                                disabled={addingTargets}
                              />
                              <Button
                                variant="primary"
                                modifier="solid"
                                label={
                                  addingTargets
                                    ? 'Adding…'
                                    : `Add ${selectedNewCount} selected`
                                }
                                icon="add"
                                iconPosition="left"
                                onClick={handleAddSelected}
                                disabled={
                                  addingTargets || selectedNewCount === 0
                                }
                              />
                            </HStack>
                          </>
                        )}
                        {previewErrors.map((message) => (
                          <InlineNotice
                            key={message}
                            errorClass="rdst-service"
                            title="Discovery issue"
                            message={message}
                            trustworthy="Already-imported targets are unaffected."
                          />
                        ))}
                      </VStack>
                    </Show>
                  </>
                )}
              </Scrollable>
            </div>
          </DrawerContent>
        )}
      </DrawerContentContainer>
    </Drawer>
  )
}
