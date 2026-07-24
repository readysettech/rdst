/**
 * Configure page - Manage database connection targets
 */

import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useLocation, useRouter } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ConfigureForm,
  ConfigureTargetList,
  DevSettingsSection,
  SettingsSection,
} from '../components/configure'
import { EnvSecretsDialog } from '../components/EnvSecretsDialog'
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
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import { useAnthropicValidity } from '../lib/useAnthropicValidity'
import { useConfigure } from '../lib/useConfigure'
import { useSystemStatus } from '../lib/useSystemStatus'
import type {
  ConfigureFormData,
  ConfigureTargetDetail,
} from '../types/configure'

// Deep-link params for the routable notices (configure-and-identity return
// trips): `edit` opens a connection's edit form, `section=ai` focuses the AI
// key card, and `returnTo` sends the user back to the feature after the fix.
// Parse-only — never throw here (keeps the app shell intact; B1 lesson).
type ConfigureSearch = {
  edit?: string
  section?: 'ai'
  returnTo?: string
}

export const Route = createFileRoute('/configure')({
  validateSearch: (search: Record<string, unknown>): ConfigureSearch => ({
    edit: typeof search.edit === 'string' ? search.edit : undefined,
    section: search.section === 'ai' ? 'ai' : undefined,
    returnTo: typeof search.returnTo === 'string' ? search.returnTo : undefined,
  }),
  component: ConfigurePage,
})

function keyValidationVariant(
  v: AnthropicKeyValidation
): 'positive' | 'negative' | 'warning' {
  if (v.valid) return 'positive'
  if (v.reason === 'rejected') return 'negative'
  return 'warning'
}

function keyValidationLabel(v: AnthropicKeyValidation): string {
  if (v.valid) return 'Key is valid — Anthropic accepted it.'
  switch (v.reason) {
    case 'rejected':
      return 'Key rejected by Anthropic. Update it with a valid key.'
    case 'no_key':
      return 'No Anthropic key is configured yet.'
    default:
      return "Couldn't reach Anthropic to verify the key. Check your connection and try again."
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
  const search = Route.useSearch()
  // Router-reactive hash (TanStack strips the leading "#"): drives the
  // /configure#dev deep-link so the effect fires once the redirect's hash is
  // applied, not racily at mount.
  const locationHash = useLocation({ select: (l) => l.hash })
  const [showForm, setShowForm] = useState(false)
  const [editingTarget, setEditingTarget] =
    useState<ConfigureTargetDetail | null>(null)
  const [showAnthropicDialog, setShowAnthropicDialog] = useState(false)
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
  // Which connection is being tested (drives the inline per-row spinner), and
  // whether the current inline result has been dismissed.
  const [testingTarget, setTestingTarget] = useState<string | null>(null)
  const [testDismissed, setTestDismissed] = useState(false)
  const deepLinkHandledRef = useRef(false)

  const {
    listTargets,
    getTarget,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    state,
    targets,
    connectionTestResult,
    error,
    loading,
  } = useConfigure()

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
    ? 'Your trial has run out. Add your own Anthropic API key to continue using AI analysis.'
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
      startBootstrapRun(data.name, { deploy: data.deploy })
    }
    setShowForm(false)
    setEditingTarget(null)
    // If we arrived here to fix a connection, resume the feature we came from.
    returnToFeature()
  }

  const handleFormCancel = () => {
    setShowForm(false)
    setEditingTarget(null)
  }

  // Confirmation is handled by the styled ConfirmDialog inside the list.
  const handleDelete = async (targetName: string) => {
    await removeTarget(targetName)
  }

  const handleSetDefault = async (targetName: string) => {
    await setDefaultTarget(targetName)
  }

  const handleTest = (targetName: string) => {
    setTestingTarget(targetName)
    setTestDismissed(false)
    testConnection(targetName)
  }

  // Drop the per-row spinner once the test settles (a result arrived, or it
  // errored without one). Render-gates the loading state to the tested row.
  useEffect(() => {
    if (connectionTestResult || state === 'error') {
      setTestingTarget(null)
    }
  }, [connectionTestResult, state])

  const visibleTestResult = testDismissed ? null : connectionTestResult

  const handleTestKey = () => {
    void keyValidityQuery.refetch()
  }

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
          title="Database connections"
          description="The databases RDST can analyze. Add one and test it connects."
          action={
            !showForm ? (
              <Button
                variant="rising"
                modifier="solid"
                icon="add"
                iconPosition="left"
                label="Add Target"
                onClick={handleAddClick}
              />
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
                <Alert
                  variant="negative"
                  modifier="outline"
                  label={`Error: ${error}`}
                />
              </m.div>
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
                  onCancel={handleFormCancel}
                  isLoading={loading}
                />
              </m.div>
            </Show>

            {/* The list — and, at zero targets, its polished hero empty-state
                (the "Add your first connection" surface) render here. [VIS-102].
                The connection-test result renders inline under the tested row. */}
            <Show when={!showForm}>
              <m.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.1 }}
              >
                <ConfigureTargetList
                  targets={targets}
                  onEdit={(target) => void handleEditClick(target.name)}
                  onTest={handleTest}
                  onDelete={handleDelete}
                  onSetDefault={handleSetDefault}
                  onAdd={handleAddClick}
                  isLoading={loading}
                  connectionTestResult={visibleTestResult}
                  testingTargetName={testingTarget}
                  onDismissTestResult={() => setTestDismissed(true)}
                />
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
                      recover your token.
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
          // Key changed: drop the cached validity verdict and re-check so the
          // status line reflects the new key, not the old result.
          void queryClient.invalidateQueries({
            queryKey: ['anthropic-validity'],
          })
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
          void queryClient.invalidateQueries({
            queryKey: ['anthropic-validity'],
          })
          returnToFeature()
        }}
      />
    </div>
  )
}
