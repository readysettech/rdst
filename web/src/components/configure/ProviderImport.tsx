/**
 * The add-database dialog's provider import: discover AWS RDS/Aurora databases,
 * discover Supabase, Neon or DigitalOcean databases, or import a CSV, then set
 * credentials for whatever landed.
 *
 * Discovery is preview-first: everything found is listed, the user picks, and
 * only the picked databases are added. Passwords never travel in the CSV; the
 * credentials step right after the import is where they get set.
 */

import { Alert } from '@rs/ui-new/alert'
import { BaseInputCheckbox } from '@rs/ui-new/base-input-checkbox'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Scrollable } from '@rs/ui-new/scrollable'
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
import { useNavigate } from '@tanstack/react-router'
import { type ComponentType, useEffect, useMemo, useRef, useState } from 'react'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import {
  PrerequisitesChecker,
  TargetsPrerequisites,
} from '../../features/prerequisites'
import { fetchEnvRequirements } from '../../lib/api'
import { downloadCsv } from '../../lib/csv'
import { useMonitoringLimit } from '../../lib/monitor/hooks'
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
  DigitalOceanLogo,
  NeonLogo,
  SupabaseLogo,
} from '../providers/ProviderLogos'
import { SupabaseConnectionPanel } from '../supabase/SupabaseConnectionPanel'
import { type DatabasesChange, notifyDatabasesChanged } from './addDatabase'
import { ADD_TABS, type AddTab } from './addTabs'
import { CredentialsStep } from './CredentialsStep'
import { groupPrivateTargets, type PrivateTargetGroup } from './privateTargets'
import { groupTargets } from './TargetGroupView'

const CSV_TEMPLATE =
  'name,host,port,database,user,engine\n' +
  'prod-orders,orders.abc1.us-east-1.rds.amazonaws.com,5432,orders,app_ro,postgresql\n' +
  'analytics,analytics.internal,3306,metrics,readonly,mysql'

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
  engine ? (ENGINE_LABELS[engine.toLowerCase()] ?? engine) : 'Database'

/** An Aurora reader: RDST monitors a cluster through its writer, so readers wait. */
const isAuroraReader = (member: DiscoveredFleetMember) =>
  (member.tags ?? []).includes('role:reader')

/** A discovered database that can be checked for import. */
const selectable = (member: DiscoveredFleetMember) =>
  !member.already_exists && !isAuroraReader(member)

/** "role:writer" -> "writer"; other tags return null. */
const roleOfTag = (tag: string): string | null =>
  tag.startsWith('role:') ? tag.slice('role:'.length) : null

/** The account providers, whose discovery reads one connected account. */
type AccountTab = Exclude<AddTab, 'aws' | 'csv'>

interface Source {
  label: string
  description: string
  /** Shown when discovery came back with nothing. */
  emptyMessage?: string
  /** The footer caption until the provider is connected. */
  signedOutCaption?: string
  /** Absent for AWS, whose discovery is driven by a region form instead. */
  account?: {
    fetchStatus: () => Promise<{ connected: boolean }>
    Panel: ComponentType<{ actions?: HTMLElement | null }>
  }
}

const SOURCES: Record<AddTab, Source> = {
  aws: {
    label: 'AWS',
    description: 'RDS and Aurora databases',
    emptyMessage: 'No databases found in the selected regions.',
    signedOutCaption:
      'Sign in with AWS SSO to discover databases, or go back and paste a connection string.',
  },
  supabase: {
    label: 'Supabase',
    description: 'Projects from your account',
    emptyMessage: 'No projects found in your Supabase organizations.',
    signedOutCaption:
      'Sign in with Supabase to discover databases, or go back and paste a connection string.',
    account: {
      fetchStatus: fetchFleetSupabaseStatus,
      Panel: SupabaseConnectionPanel,
    },
  },
  neon: {
    label: 'Neon',
    description: 'Databases from a Neon API key',
    emptyMessage: 'No projects found in your Neon account.',
    signedOutCaption: 'Connect a Neon API key to discover databases.',
    account: { fetchStatus: fetchFleetNeonStatus, Panel: NeonConnectionPanel },
  },
  digitalocean: {
    label: 'DigitalOcean',
    description: 'Managed PostgreSQL and MySQL',
    emptyMessage: 'No databases found in your DigitalOcean account.',
    signedOutCaption:
      'Sign in with DigitalOcean to discover databases, or go back and paste a connection string.',
    account: {
      fetchStatus: fetchFleetDigitaloceanStatus,
      Panel: DigitalOceanConnectionPanel,
    },
  },
  csv: {
    label: 'CSV file',
    description: 'A prepared list of database connections',
  },
}

/** "AWS", "CSV file": the source's name, for the dialog's title. */
export const sourceLabel = (tab: AddTab): string => SOURCES[tab].label

export type ImportStep = 'source' | 'credentials' | 'prereqs'

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
          <div className="rounded-control bg-surface-layout-2">
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
                  <Text
                    key={`err-${index}`}
                    level="caption"
                    className="text-content-negative-soft"
                  >
                    {message}
                  </Text>
                ))}
                {result && (
                  <Text
                    level="label-small"
                    className={`pt-2 ${result.success ? 'text-content-layout-1' : 'text-content-negative-soft'}`}
                  >
                    {result.imported} imported, {result.skipped} skipped,{' '}
                    {result.errors} errors
                  </Text>
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

const LOGOS: Record<AddTab, ComponentType<{ size?: number }> | null> = {
  aws: AwsLogo,
  supabase: SupabaseLogo,
  neon: NeonLogo,
  digitalocean: DigitalOceanLogo,
  csv: null,
}

/** The providers, one quiet row each: logo, name, one caption line, chevron. */
export function SourceList({ onPick }: { onPick: (tab: AddTab) => void }) {
  return (
    <div className="-mx-2.5 flex flex-col">
      {ADD_TABS.map((tab) => {
        const Logo = LOGOS[tab]
        return (
          <InteractiveRow
            key={tab}
            label={SOURCES[tab].label}
            onClick={() => onPick(tab)}
            className="rounded-control px-2.5 py-2 text-left hover:bg-surface-layout-2"
          >
            <span className="grid grid-cols-[1.75rem_1fr_auto] items-center gap-3">
              <span className="flex h-7 w-7 items-center justify-center rounded-control border border-border-layout-2 bg-surface-layout-2 text-content-layout-2">
                {Logo ? (
                  <Logo size={16} />
                ) : (
                  <Icon name="folder-file" label="" size="small" />
                )}
              </span>
              <span className="min-w-0">
                <Text
                  as="span"
                  level="body-small"
                  className="block text-content-layout-1"
                >
                  {SOURCES[tab].label}
                </Text>
                <Text
                  as="span"
                  level="caption"
                  className="block text-content-layout-3"
                >
                  {SOURCES[tab].description}
                </Text>
              </span>
              <Icon
                name="chevron-right"
                label=""
                size="small"
                className="text-content-layout-3"
              />
            </span>
          </InteractiveRow>
        )
      })}
    </div>
  )
}

export interface ProviderImportProps {
  /** The source to import from. */
  initialTab: AddTab
  /** The flow is over: close the dialog. */
  onClose: () => void
  /** The dialog titles each step. */
  onStepChange?: (step: ImportStep) => void
}

export function ProviderImport({
  initialTab,
  onClose,
  onStepChange,
}: ProviderImportProps) {
  const queryClient = useQueryClient()
  const [step, setStepState] = useState<ImportStep>('source')
  const setStep = (next: ImportStep) => {
    setStepState(next)
    onStepChange?.(next)
  }
  const addTab = initialTab
  const [credentialTargetNames, setCredentialTargetNames] = useState<string[]>(
    []
  )
  const [privateTargetGroups, setPrivateTargetGroups] = useState<
    PrivateTargetGroup[]
  >([])

  const { data: fleetTargets } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
  })
  const { data: envRequirements } = useQuery({
    queryKey: ['env-requirements'],
    queryFn: fetchEnvRequirements,
    staleTime: 5_000,
  })
  // Supabase, Neon and DigitalOcean each discover from one connected account:
  // no region form, and a single Discover button armed by that provider's
  // connection.
  const accountTab: AccountTab | undefined = SOURCES[addTab].account
    ? (addTab as AccountTab)
    : undefined
  // Shares its cache entry with that provider's connection panel, so signing in
  // there arms the Discover button here.
  const { data: accountStatus } = useQuery({
    queryKey: [`fleet-${accountTab}-status`],
    queryFn: () => SOURCES[accountTab as AccountTab].account?.fetchStatus(),
    staleTime: 300_000,
    refetchOnWindowFocus: false,
    retry: false,
    enabled: !!accountTab,
  })
  const accountConnected = Boolean(accountStatus?.connected)
  const AccountPanel = accountTab && SOURCES[accountTab].account?.Panel

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
  // The footer the connection panels render their buttons into, and whether the AWS
  // panel's profile wizard holds it.
  const [actions, setActions] = useState<HTMLDivElement | null>(null)
  const [awsWizard, setAwsWizard] = useState(false)

  // Shares the AWS connection panel's cache entry (same key + profile), so
  // signing in or out there arms or disarms the Discover button here.
  const { data: awsStatus } = useQuery({
    queryKey: ['fleet-aws-status', discoverProfile],
    queryFn: () => fetchFleetAwsStatus(discoverProfile || undefined),
    staleTime: 5_000,
    retry: false,
    enabled: addTab === 'aws',
  })
  const awsConnected = Boolean(awsStatus?.has_credentials)
  // The provider driving the active source's discovery: AWS by its credentials,
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
    .filter((member) => selectable(member))
    .map((member) => member.name)
  const allPreviewSelected =
    selectablePreviewNames.length > 0 &&
    selectablePreviewNames.every((name) => selectedNames.has(name))

  const selectedNewCount = previewMembers
    ? previewMembers.filter(
        (member) => selectedNames.has(member.name) && selectable(member)
      ).length
    : 0

  // Newly-added databases must reach every consumer at once: the list this
  // import reads, the sidebar's switcher, and pages that list databases.
  const publishNewTargets = async (change: DatabasesChange = {}) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['fleet-targets'] }),
      queryClient.invalidateQueries({ queryKey: ['status'] }),
      queryClient.invalidateQueries({ queryKey: ['init-status'] }),
    ])
    notifyDatabasesChanged(change)
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
            .filter((member) => selectable(member))
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

  // A connected account discovers on its own, once per visit, so signing in leads
  // straight to the database list.
  const autoDiscovered = useRef<AccountTab | null>(null)
  useEffect(() => {
    if (!accountTab || !accountConnected) return
    if (previewMembers !== null || previewLoading) return
    if (autoDiscovered.current === accountTab) return
    autoDiscovered.current = accountTab
    void handleDiscoverPreview({ provider: accountTab })
  })

  const handleAddSelected = async () => {
    if (!previewMembers) return
    const chosen = previewMembers.filter(
      (member) => selectedNames.has(member.name) && selectable(member)
    )
    if (chosen.length === 0) return
    setAddingTargets(true)
    try {
      const added = await bulkAddFleetTargets(chosen)
      await publishNewTargets()
      void queryClient.invalidateQueries({ queryKey: ['env-requirements'] })
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

  const navigate = useNavigate()
  const finish = () => {
    // Freshly saved credentials should reflect in every list immediately, rather
    // than leaving the rows unreachable from before the passwords existed.
    if (step === 'credentials' || step === 'prereqs')
      void publishNewTargets({ recheckAll: true })
    onClose()
  }
  // A single new database ends on its Database page with the turn-on dialog open.
  const turnOnMonitoring = (target: string) => {
    finish()
    selectGlobalTarget(target)
    void navigate({ to: '/database', search: { panel: 'enable' } })
  }
  // Several new databases end on the first one monitoring was turned on for.
  const finishImport = (monitored?: string) => {
    finish()
    if (!monitored) return
    selectGlobalTarget(monitored)
    void navigate({ to: '/database' })
  }

  const credentialTargets = (fleetTargets?.members ?? []).filter((member) =>
    credentialTargetNames.includes(member.name)
  )

  if (step === 'prereqs' || step === 'credentials')
    return (
      <Scrollable rootClassName="min-h-0" className="px-6 pt-3 pb-5">
        {step === 'credentials' ? (
          <CredentialsStep
            targets={credentialTargets}
            keyringAvailable={envRequirements?.keyring_available ?? false}
            onClose={finish}
            onDone={() => setStep('prereqs')}
            onSaved={(names) => publishNewTargets({ recheck: names })}
            privateTargetGroups={privateTargetGroups}
          />
        ) : credentialTargetNames.length === 1 ? (
          <PrerequisitesChecker
            target={credentialTargetNames[0]}
            layout="panel"
            onContinue={() => turnOnMonitoring(credentialTargetNames[0])}
            continueLabel="Turn on monitoring"
            onSkip={finish}
            skipLabel="Set up later"
          />
        ) : (
          <TargetsPrerequisites
            targets={credentialTargetNames}
            onDone={finishImport}
            onSkip={finish}
          />
        )}
      </Scrollable>
    )

  // Signed out, the provider's own sign-in is the footer's one solid; Discover takes
  // over once it is connected.
  const canDiscover =
    previewMembers === null &&
    (accountTab ? accountConnected : awsConnected && !awsWizard)
  const caption =
    addTab === 'csv'
      ? (csvUpload?.name ??
        'Passwords are set in the next step, never in the CSV.')
      : previewMembers !== null
        ? 'Next, set credentials for the databases you add.'
        : !activeConnected || awsWizard
          ? SOURCES[addTab].signedOutCaption
          : 'You choose which databases to add.'

  return (
    <>
      <Scrollable rootClassName="min-h-0" className="px-6 pt-2 pb-5">
        <Show when={addTab === 'csv'}>
          <VStack className="gap-4 items-stretch">
            <Text level="caption" className="text-content-layout-3">
              Include name, host, and engine in each row. You can also set port,
              database, and user.
            </Text>
            <pre className="overflow-x-auto rounded-control bg-surface-layout-2 px-3.5 py-3 text-mono-small text-content-layout-2">
              {CSV_TEMPLATE}
            </pre>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Download template"
              className="self-start"
              onClick={() =>
                downloadCsv(`${CSV_TEMPLATE}\n`, 'rdst-databases.csv')
              }
            />
            <input
              type="file"
              accept=".csv,text/csv"
              ref={csvFileInputRef}
              className="hidden"
              onChange={(e) => void handleCsvFile(e.target.files?.[0])}
            />
            <StreamLog
              progress={importProgress}
              errors={importErrors}
              result={importResult}
              onClear={resetImport}
            />
          </VStack>
        </Show>

        {/* Every cloud provider shares everything below the connection panel:
            only the discover form differs. */}
        <Show when={addTab !== 'csv'}>
          <VStack className="gap-4 items-stretch">
            {AccountPanel ? (
              <AccountPanel actions={actions} />
            ) : (
              <AwsConnectionPanel
                profile={discoverProfile}
                onProfileChange={setDiscoverProfile}
                onRegionPrefill={(region) => {
                  if (!PRESET_REGIONS.includes(region)) {
                    setCustomRegions((current) =>
                      current.includes(region) ? current : [...current, region]
                    )
                  }
                  selectRegion(region)
                }}
                actions={actions}
                onWizardOpenChange={setAwsWizard}
              />
            )}
            <Show when={addTab === 'aws' || addTab === 'digitalocean'}>
              <Text level="caption" className="text-content-layout-3">
                For a private database, import it first, then add an SSH jump
                host in its connection details.
              </Text>
            </Show>
            {addTab === 'aws' && previewMembers === null && !awsWizard && (
              <div>
                <Text
                  as="p"
                  level="caption"
                  className="mt-2 mb-1.5 text-content-layout-3"
                >
                  Regions
                </Text>
                <HStack className="gap-1 flex-wrap">
                  {[...PRESET_REGIONS, ...customRegions].map((region) => (
                    <Button
                      key={region}
                      type="button"
                      label={region}
                      size="small"
                      aria-pressed={selectedRegions.includes(region)}
                      variant="primary"
                      // Selected regions read as chosen: raised, not just bordered.
                      modifier={
                        selectedRegions.includes(region) ? 'outline' : 'ghost'
                      }
                      onClick={() => toggleRegion(region)}
                    />
                  ))}
                </HStack>
                <div className="mt-2">
                  <BaseInputSelect
                    name="fleet-discover-add-region"
                    placeholder="Add another region"
                    value=""
                    onValueChange={promoteRegion}
                    triggerClassName="h-8.5 bg-surface-layout-2 text-body-small"
                    options={OTHER_REGIONS.filter(
                      (region) => !customRegions.includes(region)
                    ).map((region) => ({ value: region, label: region }))}
                  />
                </div>
              </div>
            )}
            {previewMembers !== null && (
              <>
                <HStack className="gap-3 items-center justify-between">
                  <Text level="body-small" className="text-content-layout-2">
                    {previewMembers.length === 0
                      ? SOURCES[addTab].emptyMessage
                      : 'Choose which databases to add.'}
                  </Text>
                  {previewMembers.some((member) => selectable(member)) && (
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      label={
                        allPreviewSelected ? 'Clear selection' : 'Select all'
                      }
                      onClick={() =>
                        setSelectedNames(
                          allPreviewSelected
                            ? new Set()
                            : new Set(selectablePreviewNames)
                        )
                      }
                    />
                  )}
                </HStack>
                <SelectionNotes
                  members={previewMembers}
                  selected={selectedNames}
                />
                <VStack className="gap-4 items-stretch">
                  {previewGroups.map(({ group, targets: rows }) => (
                    <VStack
                      key={group || 'ungrouped'}
                      className="items-stretch"
                    >
                      <HStack className="gap-2 items-center pb-2">
                        <Text
                          level="label-small"
                          className="text-content-layout-1"
                        >
                          {group || 'Ungrouped'}
                        </Text>
                        <Text level="caption" className="text-content-layout-3">
                          {rows.length === 1
                            ? '1 database'
                            : `${rows.length} databases`}
                        </Text>
                        {rows.some((member) => selectable(member)) && (
                          <Button
                            variant="primary"
                            modifier="ghost"
                            size="small"
                            className="ml-auto"
                            label={
                              rows
                                .filter((member) => selectable(member))
                                .every((member) =>
                                  selectedNames.has(member.name)
                                )
                                ? 'Clear selection'
                                : 'Select all'
                            }
                            onClick={() =>
                              setSelectedNames((current) => {
                                const updated = new Set(current)
                                const selectableRows = rows.filter((member) =>
                                  selectable(member)
                                )
                                const allSelected = selectableRows.every(
                                  (member) => current.has(member.name)
                                )
                                for (const member of selectableRows) {
                                  if (allSelected) updated.delete(member.name)
                                  else updated.add(member.name)
                                }
                                return updated
                              })
                            }
                          />
                        )}
                      </HStack>
                      <div className="border-t border-border-layout-2">
                        {rows.map((member) => (
                          <DiscoveredRow
                            key={member.name}
                            member={member}
                            showRegion={previewRegionCount > 1}
                            checked={selectedNames.has(member.name)}
                            onCheckedChange={(next) =>
                              setSelectedNames((current) => {
                                const updated = new Set(current)
                                if (next) updated.add(member.name)
                                else updated.delete(member.name)
                                return updated
                              })
                            }
                          />
                        ))}
                      </div>
                    </VStack>
                  ))}
                </VStack>
              </>
            )}
            {previewErrors.map((message) => (
              <Alert
                key={message}
                variant="negative"
                modifier="ghost"
                icon="alert"
                iconPosition="left"
                label={message}
              />
            ))}
          </VStack>
        </Show>
      </Scrollable>
      <div className="flex items-center gap-2 border-t border-border-layout-2 px-6 py-3.5">
        <Text
          as="p"
          level="caption"
          className="mr-1 min-w-0 flex-1 break-words text-content-layout-3"
        >
          {caption}
        </Text>
        <div ref={setActions} className="contents" />
        {addTab === 'csv' ? (
          <>
            <Button
              variant="primary"
              modifier="outline"
              label="Choose CSV file"
              disabled={isImporting}
              onClick={() => csvFileInputRef.current?.click()}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Import"
              aria-label="Import"
              onClick={handleImport}
              loading={isImporting}
              disabled={!csvUpload}
            />
          </>
        ) : previewMembers !== null ? (
          <>
            {addTab === 'aws' && (
              <Button
                variant="primary"
                modifier="ghost"
                label="Back to regions"
                onClick={() => setPreviewMembers(null)}
                disabled={addingTargets}
              />
            )}
            <Button
              variant="primary"
              modifier="solid"
              label={`Add ${selectedNewCount} selected`}
              aria-label={`Add ${selectedNewCount} selected`}
              onClick={handleAddSelected}
              loading={addingTargets}
              disabled={selectedNewCount === 0}
            />
          </>
        ) : (
          canDiscover && (
            <Button
              variant="primary"
              modifier="solid"
              label="Discover"
              aria-label="Discover"
              loading={previewLoading}
              disabled={addTab === 'aws' && selectedRegions.length === 0}
              onClick={() =>
                void handleDiscoverPreview(
                  accountTab
                    ? { provider: accountTab }
                    : {
                        regions: selectedRegions,
                        profile: discoverProfile || undefined,
                      }
                )
              }
            />
          )
        )}
      </div>
    </>
  )
}

/** One discovered database on a hairline: checkbox, name, one caption line. */
function DiscoveredRow({
  member,
  showRegion,
  checked,
  onCheckedChange,
}: {
  member: DiscoveredFleetMember
  showRegion: boolean
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  const role = member.tags.map(roleOfTag).find(Boolean)
  const caption = [
    `${engineLabel(member.engine)} at ${member.host}:${member.port}/${member.database}`,
    role,
    showRegion ? member.region : null,
  ]
    .filter(Boolean)
    .join(', ')
  const body = (
    // The checkbox is nested inside, behind a design-system component.
    // biome-ignore lint/a11y/noLabelWithoutControl: nested control
    <label
      className={`flex items-start gap-3 border-b border-border-layout-2 py-2.5 ${
        selectable(member) ? 'cursor-pointer' : 'opacity-60'
      }`}
    >
      <span className="mt-0.5 flex items-center shrink-0">
        {member.already_exists ? (
          <Icon
            name="tick"
            label="Already added"
            size="small"
            className="text-content-positive-soft"
          />
        ) : !selectable(member) ? (
          <Icon
            name="minus"
            label="Not available yet"
            size="small"
            className="text-content-layout-3"
          />
        ) : (
          <BaseInputCheckbox
            checked={checked}
            onCheckedChange={(next) => onCheckedChange(next === true)}
            aria-label={`Select ${member.name}`}
          />
        )}
      </span>
      <VStack className="gap-0.5 items-start min-w-0 flex-1">
        <Text level="label-small" className="text-content-layout-1">
          {member.name}
        </Text>
        <Text
          level="caption"
          className="text-content-layout-3 truncate max-w-full"
        >
          {caption}
        </Text>
      </VStack>
    </label>
  )
  if (selectable(member)) return body
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{body}</TooltipTrigger>
        <TooltipContent
          label={
            member.already_exists
              ? 'This database is already added.'
              : 'RDST will monitor an Aurora cluster as one database, readers included. For now, add the writer.'
          }
        />
      </Tooltip>
    </TooltipProvider>
  )
}

/** Why Aurora readers can't be added yet, and when the selection outgrows AI monitoring. */
function SelectionNotes({
  members,
  selected,
}: {
  members: DiscoveredFleetMember[]
  selected: Set<string>
}) {
  const limit = useMonitoringLimit()
  const readers = members.filter(
    (member) => !member.already_exists && isAuroraReader(member)
  )
  const room =
    limit.data?.known && limit.data.limit != null
      ? Math.max(0, limit.data.limit - limit.data.in_use)
      : null
  const over = room != null && selected.size > room
  if (!readers.length && !over) return null
  return (
    <VStack className="gap-1 items-stretch">
      {readers.length > 0 && (
        <Text level="caption" className="text-content-layout-3">
          RDST monitors an Aurora cluster through its writer, so add the writer;
          readers can't be added yet.
        </Text>
      )}
      {over && (
        <Text level="caption" className="text-content-warning-plain">
          {`You've selected ${selected.size} databases, and AI monitoring has room for ${room} more. The rest can run locally without AI.`}
        </Text>
      )}
    </VStack>
  )
}
