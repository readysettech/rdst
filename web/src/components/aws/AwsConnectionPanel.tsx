import { Alert } from '@rs/ui-new/alert'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  FleetAwsLoginError,
  type FleetAwsLoginStatus,
  type FleetAwsSsoAccount,
  fetchFleetAwsLogin,
  fetchFleetAwsSsoAccounts,
  fetchFleetAwsSsoRoles,
  fetchFleetAwsStatus,
  finalizeFleetAwsSsoProfile,
  fleetAwsLogout,
  startFleetAwsLogin,
  startFleetAwsSsoLogin,
} from '../../lib/useFleet'
import { Field } from '../configure/Field'
import { COMPACT_CARD, panelActions } from '../providers/cardShell'
import { AwsLogo } from '../providers/ProviderLogos'

export const awsIdentityLabel = (arn: string | null): string =>
  arn ? (arn.split('/').pop() ?? arn) : 'unknown identity'

// The role an SSO profile assumes lives in the middle of the STS ARN
// (arn:aws:sts::<account>:assumed-role/<role>/<session>); pair it with the
// account so the user can tell which profile serves which target's audit.
const awsRoleFromArn = (arn: string | null): string | null => {
  if (!arn) return null
  const match = arn.match(/assumed-role\/([^/]+)\//)
  return match ? match[1] : null
}

// The wizard's connect step and the profile picker share this: names the failure and
// offers the AWS CLI fallback command to copy. Signing in again retries.
function AwsErrorBlock({ error }: { error: FleetAwsLoginError }) {
  return (
    <VStack className="gap-2 items-stretch">
      <Alert
        variant="negative"
        modifier="ghost"
        icon="alert"
        iconPosition="left"
        label={
          error.code === 'aws_cli_missing'
            ? 'AWS CLI is not installed. Install AWS CLI v2, then try again.'
            : error.message
        }
      />
      {error.fallbackCommand && (
        <HStack className="gap-2 items-center rounded-control bg-surface-layout-2 px-3 py-2">
          <Text
            asChild
            level="mono-small"
            className="min-w-0 flex-1 break-all text-content-layout-2"
          >
            <code>{error.fallbackCommand}</code>
          </Text>
          <CopyButton text={error.fallbackCommand} />
        </HStack>
      )}
    </VStack>
  )
}

const FIELD_LABEL = 'mb-1 block text-caption text-content-layout-3'
const SELECT_TRIGGER = 'h-8.5 bg-surface-layout-2 text-body-small'

type WizardStep = 'connect' | 'account' | 'role'

// Guided SSO sign-in. The user types only a start URL + region and signs in
// through the browser; AWS then hands back the accounts and roles they can
// actually assume, so the account ID and role name are picked from dropdowns
// rather than hand-typed (hand-typed values fail late with a ForbiddenException
// at GetRoleCredentials).
function AwsSsoWizard({
  canCancel,
  onCancel,
  onConnected,
  defaultRegion,
  actions,
}: {
  canCancel: boolean
  onCancel: () => void
  onConnected: (profile: string) => Promise<unknown> | void
  defaultRegion?: string | null
  actions?: HTMLElement | null
}) {
  const accountSelectId = useId()
  const roleSelectId = useId()
  const [step, setStep] = useState<WizardStep>('connect')
  const [startUrl, setStartUrl] = useState('')
  const [region, setRegion] = useState(defaultRegion || 'us-east-1')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const [starting, setStarting] = useState(false)
  const [loginId, setLoginId] = useState<string | null>(null)
  const [loginStatus, setLoginStatus] = useState<FleetAwsLoginStatus | null>(
    null
  )
  const [signInError, setSignInError] = useState<FleetAwsLoginError | null>(
    null
  )

  const [accounts, setAccounts] = useState<FleetAwsSsoAccount[] | null>(null)
  const [accountsError, setAccountsError] = useState<string | null>(null)
  const [accountId, setAccountId] = useState('')

  const [roles, setRoles] = useState<string[] | null>(null)
  const [rolesError, setRolesError] = useState<string | null>(null)
  const [roleName, setRoleName] = useState('')

  const [finalizing, setFinalizing] = useState(false)
  const [finalizeError, setFinalizeError] = useState<string | null>(null)

  const loadAccounts = useCallback(async (start: string) => {
    setAccounts(null)
    setAccountsError(null)
    try {
      const result = await fetchFleetAwsSsoAccounts(start)
      if (result.error) {
        setAccountsError(result.error)
        setAccounts([])
      } else {
        setAccounts(result.accounts)
      }
    } catch (caught) {
      setAccountsError(
        caught instanceof Error ? caught.message : String(caught)
      )
      setAccounts([])
    }
  }, [])

  const loadRoles = useCallback(async (start: string, account: string) => {
    setRoles(null)
    setRolesError(null)
    try {
      const result = await fetchFleetAwsSsoRoles(start, account)
      if (result.error) {
        setRolesError(result.error)
        setRoles([])
      } else {
        setRoles(result.roles)
      }
    } catch (caught) {
      setRolesError(caught instanceof Error ? caught.message : String(caught))
      setRoles([])
    }
  }, [])

  const goToAccounts = useCallback(() => {
    setStep('account')
    void loadAccounts(startUrl)
  }, [loadAccounts, startUrl])

  const beginSignIn = useCallback(async () => {
    setStarting(true)
    setSignInError(null)
    setLoginStatus(null)
    try {
      const started = await startFleetAwsSsoLogin({
        start_url: startUrl,
        region,
      })
      if (started.state === 'already_signed_in' || !started.login_id) {
        goToAccounts()
        return
      }
      setLoginId(started.login_id)
      setLoginStatus({ state: 'running', detail: started.detail })
    } catch (caught) {
      setSignInError(
        caught instanceof FleetAwsLoginError
          ? caught
          : new FleetAwsLoginError(String(caught))
      )
    } finally {
      setStarting(false)
    }
  }, [startUrl, region, goToAccounts])

  useEffect(() => {
    if (!loginId) return
    let cancelled = false
    const poll = async () => {
      try {
        const status = await fetchFleetAwsLogin(loginId)
        if (cancelled) return
        setLoginStatus(status)
        if (status.state === 'success') {
          setLoginId(null)
          goToAccounts()
        } else if (status.state === 'failed' || status.state === 'timeout') {
          setLoginId(null)
          setSignInError(
            new FleetAwsLoginError(
              status.detail,
              undefined,
              status.fallback_command || undefined
            )
          )
        }
      } catch (caught) {
        if (!cancelled) {
          setLoginId(null)
          setSignInError(
            caught instanceof FleetAwsLoginError
              ? caught
              : new FleetAwsLoginError(String(caught))
          )
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [loginId, goToAccounts])

  const selectAccount = (id: string) => {
    setAccountId(id)
    setRoleName('')
    setFinalizeError(null)
    setStep('role')
    void loadRoles(startUrl, id)
  }

  const createProfile = useCallback(async () => {
    setFinalizing(true)
    setFinalizeError(null)
    // The profile name is an internal artifact the user never picks. Derive it
    // deterministically from the chosen role and account, matching AWS's own
    // convention (e.g. AdministratorAccess-123456789012).
    const profileName = `${roleName}-${accountId}`
    try {
      const result = await finalizeFleetAwsSsoProfile({
        name: profileName,
        start_url: startUrl,
        region,
        account_id: accountId,
        role_name: roleName,
      })
      if (result.created) await onConnected(result.profile || profileName)
    } catch (caught) {
      // A deterministic name means a conflict is a profile for this exact
      // account and role that already exists: sign in with it rather than
      // asking the user to invent a different name.
      if (
        caught instanceof FleetAwsLoginError &&
        caught.code === 'profile_exists'
      ) {
        await onConnected(profileName)
      } else {
        setFinalizeError(
          caught instanceof Error ? caught.message : String(caught)
        )
      }
    } finally {
      setFinalizing(false)
    }
  }, [startUrl, region, accountId, roleName, onConnected])

  const backToConnect = () => {
    setLoginId(null)
    setLoginStatus(null)
    setSignInError(null)
    setStep('connect')
  }

  const selectedAccount = accounts?.find((a) => a.account_id === accountId)
  const stepLabel =
    step === 'connect'
      ? 'Sign in with AWS SSO (step 1 of 3)'
      : step === 'account'
        ? 'Choose an account (step 2 of 3)'
        : 'Choose a role (step 3 of 3)'
  const cancel = canCancel && (
    <Button
      variant="primary"
      modifier="ghost"
      label="Cancel"
      onClick={onCancel}
    />
  )

  return (
    <VStack className="gap-3 items-stretch">
      <Text
        as="p"
        level="caption"
        className="mt-2 mb-1.5 text-content-layout-3"
      >
        {stepLabel}
      </Text>

      {loginId ? (
        <VStack className="gap-2 items-start">
          <HStack className="gap-2 items-center">
            <Spinner size="base" />
            <Text level="body-small" className="text-content-layout-1">
              Complete the sign-in in the browser window we opened
            </Text>
          </HStack>
          <Text level="caption" className="text-content-layout-3">
            {loginStatus?.detail || 'Waiting for AWS SSO...'}
          </Text>
          {loginStatus?.verification_url && (
            <Text asChild level="body-small">
              <a
                href={loginStatus.verification_url}
                target="_blank"
                rel="noreferrer"
                className="text-content-primary-soft underline-offset-4 hover:underline"
              >
                Open the AWS verification page
              </a>
            </Text>
          )}
        </VStack>
      ) : step === 'connect' ? (
        <VStack className="gap-3 items-stretch">
          <Text level="body-small" className="text-content-layout-2">
            Paste your AWS access portal start URL and sign in through the
            browser. We handle the rest.
          </Text>
          <Field
            label="Start URL"
            name="aws-sso-start-url"
            value={startUrl}
            placeholder="https://my-company.awsapps.com/start"
            helper="Your organization's AWS access portal, from IAM Identity Center or your admin."
            onChange={setStartUrl}
          />
          <div>
            <Button
              type="button"
              size="small"
              modifier="link"
              label={showAdvanced ? 'Hide advanced' : 'Advanced'}
              classMerge="h-auto p-0"
              onClick={() => setShowAdvanced((v) => !v)}
            />
            {showAdvanced && (
              <Field
                label="Region"
                name="aws-sso-region"
                value={region}
                placeholder="us-east-1"
                helper="AWS region of your IAM Identity Center."
                onChange={setRegion}
                className="mt-2"
              />
            )}
          </div>
        </VStack>
      ) : step === 'account' ? (
        accounts === null ? (
          <HStack className="gap-2 items-center">
            <Spinner size="base" />
            <Text level="caption" className="text-content-layout-3">
              Loading accounts...
            </Text>
          </HStack>
        ) : accountsError ? (
          <Alert
            variant="negative"
            modifier="ghost"
            icon="alert"
            iconPosition="left"
            label={accountsError}
          />
        ) : (
          <div>
            <label htmlFor={accountSelectId} className={FIELD_LABEL}>
              Account
            </label>
            <BaseInputSelect
              id={accountSelectId}
              name="aws-sso-account"
              value={accountId}
              placeholder="Select an account"
              onValueChange={selectAccount}
              triggerClassName={SELECT_TRIGGER}
              options={accounts.map((account) => ({
                value: account.account_id,
                label: `${account.account_name} (${account.account_id})`,
              }))}
            />
          </div>
        )
      ) : (
        <VStack className="gap-3 items-stretch">
          {selectedAccount && (
            <Text level="caption" className="text-content-layout-3">
              Account {selectedAccount.account_name} (
              {selectedAccount.account_id})
            </Text>
          )}
          {roles === null ? (
            <HStack className="gap-2 items-center">
              <Spinner size="base" />
              <Text level="caption" className="text-content-layout-3">
                Loading roles...
              </Text>
            </HStack>
          ) : rolesError ? (
            <Alert
              variant="negative"
              modifier="ghost"
              icon="alert"
              iconPosition="left"
              label={rolesError}
            />
          ) : (
            <div>
              <label htmlFor={roleSelectId} className={FIELD_LABEL}>
                Role
              </label>
              <BaseInputSelect
                id={roleSelectId}
                name="aws-sso-role"
                value={roleName}
                placeholder="Select a role"
                onValueChange={setRoleName}
                triggerClassName={SELECT_TRIGGER}
                options={roles.map((role) => ({ value: role, label: role }))}
              />
            </div>
          )}
          {finalizeError && (
            <Alert
              variant="negative"
              modifier="ghost"
              icon="alert"
              iconPosition="left"
              label={finalizeError}
            />
          )}
        </VStack>
      )}

      {signInError && <AwsErrorBlock error={signInError} />}

      {panelActions(
        actions,
        step === 'connect' ? (
          <>
            {cancel}
            <Button
              variant="primary"
              modifier="solid"
              label="Sign in with AWS SSO"
              aria-label="Sign in with AWS SSO"
              loading={starting || Boolean(loginId)}
              disabled={!startUrl.trim() || !region.trim()}
              onClick={() => void beginSignIn()}
            />
          </>
        ) : step === 'account' ? (
          <>
            <Button
              variant="primary"
              modifier="ghost"
              label="Back to start URL"
              onClick={backToConnect}
            />
            {cancel}
          </>
        ) : (
          <>
            <Button
              variant="primary"
              modifier="ghost"
              label="Back to accounts"
              onClick={goToAccounts}
            />
            {cancel}
            <Button
              variant="primary"
              modifier="solid"
              label="Create profile and connect"
              aria-label="Create profile and connect"
              loading={finalizing}
              disabled={!roleName}
              onClick={() => void createProfile()}
            />
          </>
        )
      )}
    </VStack>
  )
}

export function AwsConnectionPanel({
  enabled = true,
  profile,
  onProfileChange,
  onRegionPrefill,
  compact = false,
  onSignIn,
  actions,
  onWizardOpenChange,
}: {
  enabled?: boolean
  profile?: string
  onProfileChange?: (profile: string) => void
  onRegionPrefill?: (region: string) => void
  /** Settings-page rendering: one quiet row per connection state. */
  compact?: boolean
  /** Where the compact signed-out row sends the user to actually sign in. */
  onSignIn?: () => void
  /** The host dialog's footer, where the panel's buttons render. */
  actions?: HTMLElement | null
  /** The create-profile wizard opened or closed. */
  onWizardOpenChange?: (open: boolean) => void
}) {
  const profileSelectId = useId()
  const [localProfile, setLocalProfile] = useState('')
  const selectedProfile = profile ?? localProfile
  const setProfile = onProfileChange ?? setLocalProfile
  const [loginId, setLoginId] = useState<string | null>(null)
  const [loginStatus, setLoginStatus] = useState<FleetAwsLoginStatus | null>(
    null
  )
  const [loginError, setLoginError] = useState<FleetAwsLoginError | null>(null)
  const [starting, setStarting] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const [showAddProfile, setShowAddProfile] = useState(false)

  const {
    data: aws,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['fleet-aws-status', selectedProfile],
    queryFn: () => fetchFleetAwsStatus(selectedProfile || undefined),
    enabled,
    staleTime: 5_000,
    retry: false,
  })

  const didPrefill = useRef(false)
  useEffect(() => {
    if (!aws) return
    // Commit a concrete profile to state up front: the select's displayed
    // default is not a selection, and everything downstream (status checks,
    // sign-in, discovery) must agree on one real profile.
    if (!selectedProfile) {
      const initial = aws.active_profile || aws.available_profiles[0]
      if (initial) setProfile(initial)
    }
    if (!didPrefill.current && aws.region) {
      didPrefill.current = true
      onRegionPrefill?.(aws.region)
    }
  }, [aws, onRegionPrefill, selectedProfile, setProfile])

  // With no profiles and no session there is nothing to pick, so open the
  // create-profile wizard up front. Once open it stays open (below) until the
  // user finishes or cancels, so a mid-login flip to signed-in never closes it.
  useEffect(() => {
    if (compact || !aws) return
    if (aws.available_profiles.length === 0 && !aws.has_credentials) {
      setShowAddProfile(true)
    }
  }, [compact, aws])

  useEffect(() => {
    onWizardOpenChange?.(!compact && showAddProfile)
  }, [compact, showAddProfile, onWizardOpenChange])

  const beginLogin = useCallback(
    async (profileName: string) => {
      if (!profileName) return
      setStarting(true)
      setLoginError(null)
      setLoginStatus(null)
      try {
        const started = await startFleetAwsLogin(profileName)
        if (started.state === 'already_signed_in') {
          await refetch()
          return
        }
        setLoginId(started.login_id)
        setLoginStatus({ state: 'running', detail: started.detail })
      } catch (caught) {
        setLoginError(
          caught instanceof FleetAwsLoginError
            ? caught
            : new FleetAwsLoginError(String(caught))
        )
      } finally {
        setStarting(false)
      }
    },
    [refetch]
  )

  useEffect(() => {
    if (!loginId) return
    let cancelled = false
    const poll = async () => {
      try {
        const status = await fetchFleetAwsLogin(loginId)
        if (cancelled) return
        setLoginStatus(status)
        if (status.state === 'success') {
          setLoginId(null)
          await refetch()
        } else if (status.state === 'failed' || status.state === 'timeout') {
          setLoginId(null)
          setLoginError(
            new FleetAwsLoginError(
              status.detail,
              undefined,
              status.fallback_command || undefined
            )
          )
        }
      } catch (caught) {
        if (!cancelled) {
          setLoginId(null)
          setLoginError(
            caught instanceof FleetAwsLoginError
              ? caught
              : new FleetAwsLoginError(String(caught))
          )
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [loginId, refetch])

  if (!enabled) return null
  if (isLoading) {
    const checking = (
      <HStack className="gap-2 items-center">
        <Spinner size="base" />
        <Text level="caption" className="text-content-layout-3">
          Checking AWS credentials...
        </Text>
      </HStack>
    )
    return compact ? (
      <div className={COMPACT_CARD} data-testid="aws-connection-panel">
        {checking}
      </div>
    ) : (
      <div data-testid="aws-connection-panel">{checking}</div>
    )
  }
  // A provider the user may never touch stays silent about its own failures
  // when it is only a settings row.
  if (isError)
    return compact ? null : (
      <InlineNotice
        errorClass="rdst-service"
        title="Couldn't check AWS credentials"
        message={error instanceof Error ? error.message : String(error)}
        trustworthy="Your databases and saved reports are unaffected."
        onRetry={() => void refetch()}
      />
    )
  if (!aws) return null

  // The wizard owns the create-profile flow once open. It renders ahead of the
  // signed-in and picker branches because the SSO token starts yielding
  // credentials the moment the browser login completes -- before the user has
  // picked an account -- and the panel must not flip to signed-in and unmount
  // the account/role steps out from under them. The compact settings card
  // routes into the drawer, so it never opens the wizard here.
  if (!compact && showAddProfile) {
    return (
      <div data-testid="aws-connection-panel">
        <AwsSsoWizard
          canCancel={aws.available_profiles.length > 0 || aws.has_credentials}
          onCancel={() => setShowAddProfile(false)}
          onConnected={async (createdProfile) => {
            setShowAddProfile(false)
            setProfile(createdProfile)
            await refetch()
          }}
          defaultRegion={aws.region}
          actions={actions}
        />
      </div>
    )
  }

  if (aws.has_credentials) {
    const signOut = () => {
      setSigningOut(true)
      void fleetAwsLogout()
        .catch(() => {})
        .then(() => refetch())
        .finally(() => setSigningOut(false))
    }
    // Which account and role the active profile maps to, so the user can tell
    // which profile serves a given target's health check (discovery tags
    // targets with the account, and the audit preflight blocks targets whose
    // account differs from the signed-in one).
    const role = awsRoleFromArn(aws.identity_arn)
    const identityDetail =
      role && aws.account
        ? `Role ${role} in account ${aws.account}`
        : role
          ? `Role ${role}`
          : aws.account
            ? `Account ${aws.account}`
            : null
    // The profile select, with the account and role it maps to under it.
    const profileField = (className: string) =>
      (aws.available_profiles.length > 0 || identityDetail) && (
        <div className={className}>
          {aws.available_profiles.length > 0 && (
            <>
              <label htmlFor={profileSelectId} className={FIELD_LABEL}>
                Profile
              </label>
              <BaseInputSelect
                id={profileSelectId}
                name="fleet-discover-profile"
                value={selectedProfile}
                onValueChange={setProfile}
                // Profile names run long (e.g. AdministratorAccess-<account>); keep
                // the value on one truncated, left-aligned line.
                triggerClassName={`${SELECT_TRIGGER} [&>span]:min-w-0 [&>span]:text-left [&>span]:whitespace-nowrap`}
                options={aws.available_profiles.map((name) => ({
                  value: name,
                  label: name,
                }))}
              />
            </>
          )}
          {identityDetail && (
            <Text as="p" level="caption" className="mt-1 text-content-layout-3">
              {identityDetail}
            </Text>
          )}
        </div>
      )
    if (compact)
      return (
        <div className={COMPACT_CARD} data-testid="aws-connection-panel">
          <HStack className="gap-2 items-center">
            <AwsLogo size={20} className="text-content-layout-2" />
            <Text level="label-small" className="text-content-layout-1">
              AWS
            </Text>
          </HStack>
          <HStack className="gap-1.5 items-start">
            <Icon
              name="tick-double"
              label="Signed in"
              size="small"
              className="shrink-0 text-content-positive-soft"
            />
            <Text level="caption" className="text-content-positive-soft">
              Signed in to AWS as {awsIdentityLabel(aws.identity_arn)}
            </Text>
          </HStack>
          {profileField('w-full')}
          <Button
            variant="primary"
            modifier="outline"
            fullWidth
            className="mt-auto"
            icon="logout"
            iconPosition="left"
            label="Sign out"
            loading={signingOut}
            onClick={signOut}
          />
        </div>
      )
    return (
      <VStack
        className="gap-3 items-stretch"
        data-testid="aws-connection-panel"
      >
        <HStack className="gap-2 items-center">
          <AwsLogo size={16} className="shrink-0 text-content-layout-2" />
          <Text
            level="body-small"
            className="min-w-0 flex-1 truncate text-content-layout-1"
          >
            Signed in to AWS as {awsIdentityLabel(aws.identity_arn)}
          </Text>
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="logout"
            label="Sign out"
            loading={signingOut}
            onClick={signOut}
          />
        </HStack>
        {profileField('w-full')}
        <Button
          type="button"
          size="small"
          modifier="link"
          label="Add another account or role"
          classMerge="h-auto self-start p-0"
          onClick={() => setShowAddProfile(true)}
        />
      </VStack>
    )
  }

  // Signed out and only a settings card: name the provider, say what signing in
  // buys, and give one obvious way in.
  if (compact)
    return (
      <div className={COMPACT_CARD} data-testid="aws-connection-panel">
        <HStack className="gap-2 items-center">
          <AwsLogo size={20} className="text-content-layout-2" />
          <Text level="label-small" className="text-content-layout-1">
            AWS
          </Text>
        </HStack>
        <Text level="caption" className="text-content-layout-3">
          Import RDS and Aurora databases
        </Text>
        <Button
          variant="primary"
          modifier="outline"
          fullWidth
          className="mt-auto"
          label="Sign in"
          onClick={() =>
            onSignIn
              ? onSignIn()
              : void beginLogin(selectedProfile || aws.available_profiles[0])
          }
        />
      </div>
    )

  return (
    <VStack className="gap-3 items-stretch" data-testid="aws-connection-panel">
      {loginId ? (
        <VStack className="gap-2 items-start">
          <HStack className="gap-2 items-center">
            <Spinner size="base" />
            <Text level="body-small" className="text-content-layout-1">
              Complete the sign-in in the browser window we opened
            </Text>
          </HStack>
          <Text level="caption" className="text-content-layout-3">
            {loginStatus?.detail || 'Waiting for AWS SSO...'}
          </Text>
          {loginStatus?.verification_url && (
            <Text asChild level="body-small">
              <a
                href={loginStatus.verification_url}
                target="_blank"
                rel="noreferrer"
                className="text-content-primary-soft underline-offset-4 hover:underline"
              >
                Open the AWS verification page
              </a>
            </Text>
          )}
        </VStack>
      ) : aws.available_profiles.length > 0 ? (
        <>
          <HStack className="gap-2 items-start">
            <AwsLogo
              size={16}
              className="mt-0.5 shrink-0 text-content-layout-2"
            />
            <Text level="body-small" className="text-content-layout-2">
              Sign in with AWS SSO so we can read details about your imported
              databases.
            </Text>
          </HStack>
          <div>
            <label htmlFor={profileSelectId} className={FIELD_LABEL}>
              Profile
            </label>
            <BaseInputSelect
              id={profileSelectId}
              name="fleet-discover-profile"
              value={selectedProfile || aws.available_profiles[0]}
              onValueChange={setProfile}
              triggerClassName={SELECT_TRIGGER}
              options={aws.available_profiles.map((name) => ({
                value: name,
                label: name,
              }))}
            />
          </div>
          <Button
            type="button"
            size="small"
            modifier="link"
            label="Add another profile"
            classMerge="h-auto self-start p-0"
            onClick={() => setShowAddProfile(true)}
          />
        </>
      ) : (
        <AwsSsoWizard
          canCancel={false}
          onCancel={() => setShowAddProfile(false)}
          onConnected={async (createdProfile) => {
            setShowAddProfile(false)
            setProfile(createdProfile)
            await refetch()
          }}
          defaultRegion={aws.region}
          actions={actions}
        />
      )}
      {loginError && <AwsErrorBlock error={loginError} />}
      {aws.available_profiles.length > 0 &&
        panelActions(
          actions,
          <>
            <Button
              variant="primary"
              modifier="ghost"
              label="Check again"
              loading={isFetching}
              onClick={() => void refetch()}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Sign in with AWS SSO"
              aria-label="Sign in with AWS SSO"
              loading={starting || Boolean(loginId)}
              onClick={() =>
                void beginLogin(selectedProfile || aws.available_profiles[0])
              }
            />
          </>
        )}
    </VStack>
  )
}
