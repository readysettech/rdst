/**
 * Bulk-add drawer for the Settings target list: discover AWS RDS/Aurora
 * instances or import a CSV, then set credentials for whatever landed.
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
import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchEnvRequirements } from '../../lib/api'
import {
  bulkAddFleetTargets,
  type DiscoveredFleetMember,
  fetchFleetDiscoverPreview,
  fetchFleetTargets,
  useFleetImport,
} from '../../lib/useFleet'
import type {
  FleetImportCompleteEvent,
  FleetImportProgressEvent,
} from '../../types/fleet'
import { AwsConnectionPanel } from '../aws/AwsConnectionPanel'
import { CredentialsStep } from './CredentialsStep'
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

function AddTargetsTab({
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
      className="h-9 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
        data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
        data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
      data-active={active}
    >
      {label}
    </button>
  )
}

export interface AddTargetsDrawerProps {
  open: boolean
  /** Which source tab the drawer opens on. */
  initialTab?: 'aws' | 'csv'
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
  initialTab = 'aws',
  onClose,
  onTargetsAdded,
  onCredentialsClosed,
  onRecheckTargets,
}: AddTargetsDrawerProps) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<'source' | 'credentials'>('source')
  const [addTab, setAddTab] = useState<'csv' | 'aws'>(initialTab)
  const [credentialTargetNames, setCredentialTargetNames] = useState<string[]>(
    []
  )

  // Each opening starts on the requested source tab, never on a leftover step.
  useEffect(() => {
    if (!open) return
    setStep('source')
    setAddTab(initialTab)
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
  const [addingTargets, setAddingTargets] = useState(false)

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
      setStep('credentials')
    }
  }

  const handleDiscoverPreview = async () => {
    if (selectedRegions.length === 0) return
    setPreviewLoading(true)
    setPreviewErrors([])
    try {
      const preview = await fetchFleetDiscoverPreview({
        regions: selectedRegions,
        profile: discoverProfile || undefined,
      })
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

  const closeDrawer = () => {
    const leftCredentials = step === 'credentials'
    setStep('source')
    setCredentialTargetNames([])
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
                {step === 'credentials' ? 'Set credentials' : 'Add Targets'}
              </DrawerTitle>
              <DrawerDescription>
                {step === 'credentials'
                  ? 'Secure the new database targets and verify their connections.'
                  : 'Bulk-add database targets by importing a CSV or discovering AWS RDS/Aurora instances.'}
              </DrawerDescription>
              {step === 'source' && (
                <HStack className="gap-1 pt-3">
                  <AddTargetsTab
                    active={addTab === 'aws'}
                    label="Discover AWS"
                    onClick={() => setAddTab('aws')}
                  />
                  <AddTargetsTab
                    active={addTab === 'csv'}
                    label="Import CSV"
                    onClick={() => setAddTab('csv')}
                  />
                </HStack>
              )}
            </DrawerHeader>

            <Scrollable className="flex-1 p-5">
              {step === 'credentials' ? (
                <CredentialsStep
                  targets={credentialTargets}
                  keyringAvailable={envRequirements?.keyring_available ?? false}
                  onClose={closeDrawer}
                  onSaved={publishSavedCredentials}
                />
              ) : (
                <>
                  <Show when={addTab === 'csv'}>
                    <VStack className="gap-4 items-stretch">
                      <Text
                        level="body-small"
                        className="text-content-layout-3"
                      >
                        Bulk-add targets from a CSV, excluding passwords —
                        you&apos;ll set those right after the import.
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

                  <Show when={addTab === 'aws'}>
                    <VStack className="gap-4 items-stretch">
                      <AwsConnectionPanel
                        enabled={open && addTab === 'aws'}
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
                      {previewMembers === null ? (
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
                                  <button
                                    key={region}
                                    type="button"
                                    onClick={() => toggleRegion(region)}
                                    className="h-10 px-4 rounded-lg text-sm font-medium transition-all cursor-pointer border whitespace-nowrap
                                      data-[active=true]:bg-surface-primary-soft/30 data-[active=true]:border-surface-primary-solid data-[active=true]:text-content-layout-1
                                      data-[active=false]:bg-surface-layout-2 data-[active=false]:border-border-layout-1 data-[active=false]:text-content-layout-3"
                                    data-active={selectedRegions.includes(
                                      region
                                    )}
                                  >
                                    {region}
                                  </button>
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
                                  (region) => !customRegions.includes(region)
                                ).map((region) => ({
                                  value: region,
                                  label: region,
                                }))}
                              />
                            </div>
                          </div>
                          <HStack className="gap-3 items-center justify-end">
                            <Button
                              variant="primary"
                              modifier="solid"
                              label={
                                previewLoading ? 'Discovering…' : 'Discover'
                              }
                              icon="search"
                              iconPosition="left"
                              onClick={handleDiscoverPreview}
                              disabled={
                                selectedRegions.length === 0 || previewLoading
                              }
                            />
                          </HStack>
                        </>
                      ) : (
                        <>
                          <Text
                            level="body-small"
                            className="text-content-layout-2"
                          >
                            {previewMembers.length === 0
                              ? 'No databases found in the selected regions.'
                              : 'Choose which databases to add to your fleet.'}
                          </Text>
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
                                                const updated = new Set(current)
                                                if (next === true)
                                                  updated.add(member.name)
                                                else updated.delete(member.name)
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
                              label="Back to regions"
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
                              disabled={addingTargets || selectedNewCount === 0}
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
          </DrawerContent>
        )}
      </DrawerContentContainer>
    </Drawer>
  )
}
