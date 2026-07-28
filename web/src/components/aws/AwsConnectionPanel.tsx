import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
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
import { COMPACT_CARD } from '../providers/cardShell'
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

function Field({
  label,
  name,
  value,
  placeholder,
  helper,
  onChange,
}: {
  label: string
  name: string
  value: string
  placeholder?: string
  helper?: string
  onChange: (value: string) => void
}) {
  return (
    <div>
      <Text level="caption" className="text-content-layout-3 mb-1 block">
        {label}
      </Text>
      <BaseInputText
        name={name}
        value={value}
        placeholder={placeholder}
        onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
          onChange(event.target.value)
        }
      />
      {helper && (
        <Text level="caption" className="text-content-layout-3 mt-1 block">
          {helper}
        </Text>
      )}
    </div>
  )
}

// Full-width error block shared by the wizard's connect step and the profile
// picker: names the failure, offers the AWS-CLI fallback command to copy, and a
// caller-supplied retry.
function AwsErrorBlock({
  error,
  onRetry,
  retryLabel = 'Try again',
}: {
  error: FleetAwsLoginError
  onRetry: () => void
  retryLabel?: string
}) {
  return (
    <div className="rounded-lg border border-border-negative-soft bg-surface-negative-soft/20 p-3">
      <VStack className="gap-2 items-stretch">
        <Text level="label-small" className="text-content-negative-soft">
          {error.code === 'aws_cli_missing'
            ? 'AWS CLI is not installed'
            : 'AWS sign-in failed'}
        </Text>
        <Text level="body-small" className="text-content-layout-2">
          {error.code === 'aws_cli_missing'
            ? 'Install AWS CLI v2, then try again.'
            : error.message}
        </Text>
        {error.fallbackCommand && (
          <HStack className="gap-2 items-center rounded bg-surface-layout-2 px-3 py-2">
            <code className="text-xs text-content-layout-2 break-all flex-1">
              {error.fallbackCommand}
            </code>
            <CopyButton text={error.fallbackCommand} />
          </HStack>
        )}
        <HStack>
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label={retryLabel}
            onClick={onRetry}
          />
        </HStack>
      </VStack>
    </div>
  )
}

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
}: {
  canCancel: boolean
  onCancel: () => void
  onConnected: (profile: string) => Promise<unknown> | void
  defaultRegion?: string | null
}) {
  const [step, setStep] = useState<WizardStep>('connect')
  const [startUrl, setStartUrl] = useState('')
  const [region, setRegion] = useState(defaultRegion || 'us-east-1')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const [starting, setStarting] = useState(false)
  const [loginId, setLoginId] = useState<string | null>(null)
  const [loginStatus, setLoginStatus] = useState<FleetAwsLoginStatus | null>(
    null
  )
  const [signInError, setSignInError] = useState<FleetAwsLoginError | null>(null)

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
      setAccountsError(caught instanceof Error ? caught.message : String(caught))
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
    // convention (e.g. AdministratorAccess-701495964134).
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
      if (caught instanceof FleetAwsLoginError && caught.code === 'profile_exists') {
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
      ? 'Step 1 of 3 - Connect'
      : step === 'account'
        ? 'Step 2 of 3 - Choose account'
        : 'Step 3 of 3 - Choose role'

  return (
    <VStack className="gap-3 items-stretch">
      <VStack className="gap-1 items-start">
        <Text level="label-small" className="text-content-layout-1">
          Connect AWS with SSO
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {stepLabel}
        </Text>
      </VStack>

      {loginId ? (
        <VStack className="gap-3 items-start">
          <HStack className="gap-2 items-center">
            <Spinner size="base" />
            <Text level="label-small" className="text-content-layout-1">
              Complete the sign-in in the browser window we opened
            </Text>
          </HStack>
          <Text level="body-small" className="text-content-layout-2">
            {loginStatus?.detail || 'Waiting for AWS SSO...'}
          </Text>
          {loginStatus?.verification_url && (
            <a
              href={loginStatus.verification_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-content-primary-soft hover:underline"
            >
              Open the AWS verification page
            </a>
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
            helper="Your organization's AWS access portal, e.g. https://my-company.awsapps.com/start - from IAM Identity Center or your admin"
            onChange={setStartUrl}
          />
          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="cursor-pointer"
            >
              <Text
                level="caption"
                className="text-content-layout-3 hover:underline"
              >
                {showAdvanced ? 'Hide advanced' : 'Advanced'}
              </Text>
            </button>
            {showAdvanced && (
              <div className="mt-2 w-full max-w-64">
                <Field
                  label="Region"
                  name="aws-sso-region"
                  value={region}
                  placeholder="us-east-1"
                  helper="AWS region of your IAM Identity Center."
                  onChange={setRegion}
                />
              </div>
            )}
          </div>
          <HStack className="gap-2 items-center">
            <Button
              variant="primary"
              modifier="solid"
              label={starting ? 'Opening browser...' : 'Sign in with AWS'}
              disabled={starting || !startUrl.trim() || !region.trim()}
              onClick={() => void beginSignIn()}
            />
            {canCancel && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Cancel"
                onClick={onCancel}
              />
            )}
          </HStack>
        </VStack>
      ) : step === 'account' ? (
        <VStack className="gap-3 items-stretch">
          {accounts === null ? (
            <HStack className="gap-2 items-center">
              <Spinner size="base" />
              <Text level="caption" className="text-content-layout-3">
                Loading accounts...
              </Text>
            </HStack>
          ) : accountsError ? (
            <VStack className="gap-2 items-start">
              <Text level="body-small" className="text-content-negative-soft">
                {accountsError}
              </Text>
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Sign in again"
                onClick={backToConnect}
              />
            </VStack>
          ) : (
            <div>
              <Text
                level="caption"
                className="text-content-layout-3 mb-1.5 block"
              >
                Account
              </Text>
              <BaseInputSelect
                name="aws-sso-account"
                value={accountId}
                placeholder="Select an account"
                onValueChange={selectAccount}
                options={accounts.map((account) => ({
                  value: account.account_id,
                  label: `${account.account_name} (${account.account_id})`,
                }))}
              />
            </div>
          )}
          <HStack className="gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Back"
              onClick={backToConnect}
            />
            {canCancel && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Cancel"
                onClick={onCancel}
              />
            )}
          </HStack>
        </VStack>
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
            <VStack className="gap-2 items-start">
              <Text level="body-small" className="text-content-negative-soft">
                {rolesError}
              </Text>
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Choose a different account"
                onClick={goToAccounts}
              />
            </VStack>
          ) : (
            <div>
              <Text
                level="caption"
                className="text-content-layout-3 mb-1.5 block"
              >
                Role
              </Text>
              <BaseInputSelect
                name="aws-sso-role"
                value={roleName}
                placeholder="Select a role"
                onValueChange={setRoleName}
                options={roles.map((role) => ({ value: role, label: role }))}
              />
            </div>
          )}
          {finalizeError && (
            <Text level="body-small" className="text-content-negative-soft">
              {finalizeError}
            </Text>
          )}
          <HStack className="gap-2 items-center">
            <Button
              variant="primary"
              modifier="solid"
              label={
                finalizing ? 'Creating...' : 'Create profile and connect'
              }
              disabled={finalizing || !roleName}
              onClick={() => void createProfile()}
            />
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Back"
              onClick={goToAccounts}
            />
            {canCancel && (
              <Button
                variant="primary"
                modifier="ghost"
                size="small"
                label="Cancel"
                onClick={onCancel}
              />
            )}
          </HStack>
        </VStack>
      )}

      {signInError && (
        <AwsErrorBlock
          error={signInError}
          onRetry={() => {
            setSignInError(null)
            void beginSignIn()
          }}
        />
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
}: {
  enabled?: boolean
  profile?: string
  onProfileChange?: (profile: string) => void
  onRegionPrefill?: (region: string) => void
  /** Settings-page rendering: one quiet row per connection state. */
  compact?: boolean
  /** Where the compact signed-out row sends the user to actually sign in. */
  onSignIn?: () => void
}) {
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
        trustworthy="Your fleet inventory and saved reports are unaffected."
        onRetry={() => void refetch()}
        retryLabel="Re-check"
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
      <div
        data-testid="aws-connection-panel"
        className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4"
      >
        <AwsSsoWizard
          canCancel={aws.available_profiles.length > 0 || aws.has_credentials}
          onCancel={() => setShowAddProfile(false)}
          onConnected={async (createdProfile) => {
            setShowAddProfile(false)
            setProfile(createdProfile)
            await refetch()
          }}
          defaultRegion={aws.region}
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
    const identityDetail = [awsRoleFromArn(aws.identity_arn), aws.account]
      .filter(Boolean)
      .join(' - ')
    // In a Connections grid column the profile control gets its own
    // full-width row; the wider settings/drawer layout keeps it capped.
    const profileField = (className: string) =>
      aws.available_profiles.length > 0 && (
        <div className={className}>
          <Text level="caption" className="text-content-layout-3 mb-1.5 block">
            Profile
          </Text>
          <BaseInputSelect
            name="fleet-discover-profile"
            value={selectedProfile}
            onValueChange={setProfile}
            // Profile names run long (e.g. AdministratorAccess-<account>); keep
            // the value on one truncated, left-aligned line in the narrow card.
            triggerClassName="[&>span]:min-w-0 [&>span]:text-left [&>span]:whitespace-nowrap"
            options={aws.available_profiles.map((name) => ({
              value: name,
              label: name,
            }))}
          />
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
              className="w-4 h-4 text-content-positive-soft shrink-0"
            />
            <Text level="caption" className="text-content-positive-soft">
              Signed in as {awsIdentityLabel(aws.identity_arn)}
            </Text>
          </HStack>
          {identityDetail && (
            <Text level="caption" className="text-content-layout-3">
              {identityDetail}
            </Text>
          )}
          {profileField('w-full')}
          <Button
            variant="primary"
            modifier="outline"
            fullWidth
            className="mt-auto"
            icon="logout"
            iconPosition="left"
            label={signingOut ? 'Signing out...' : 'Sign out'}
            loading={signingOut}
            onClick={signOut}
          />
        </div>
      )
    return (
      <div data-testid="aws-connection-panel">
        <VStack className="gap-3 items-stretch">
          <HStack className="gap-2 items-center flex-wrap">
            <AwsLogo size={16} className="text-content-layout-2" />
            <Icon
              name="tick-double"
              label="Signed in"
              className="w-4 h-4 text-content-positive-soft shrink-0"
            />
            <Text level="caption" className="text-content-positive-soft">
              Signed into AWS as {awsIdentityLabel(aws.identity_arn)}
            </Text>
            <Button
              variant="primary"
              modifier="outline"
              size="small"
              icon="logout"
              iconPosition="left"
              label={signingOut ? 'Signing out...' : 'Sign out'}
              loading={signingOut}
              onClick={signOut}
            />
          </HStack>
          {identityDetail && (
            <Text level="caption" className="text-content-layout-3">
              {identityDetail}
            </Text>
          )}
          {profileField('w-full max-w-96 pt-1')}
          <button
            type="button"
            onClick={() => setShowAddProfile(true)}
            className="cursor-pointer self-start"
          >
            <Text
              level="caption"
              className="text-content-primary-soft hover:underline"
            >
              Add another account or role
            </Text>
          </button>
        </VStack>
      </div>
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
          Import RDS and Aurora instances
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
    <VStack
      className="gap-4 items-stretch rounded-xl border border-border-layout-1 bg-surface-layout-1 p-4"
      data-testid="aws-connection-panel"
    >
      {loginId ? (
        <VStack className="gap-3 items-start">
          <HStack className="gap-2 items-center">
            <Spinner size="base" />
            <Text level="label-small" className="text-content-layout-1">
              Complete the sign-in in the browser window we opened
            </Text>
          </HStack>
          <Text level="body-small" className="text-content-layout-2">
            {loginStatus?.detail || 'Waiting for AWS SSO…'}
          </Text>
          {loginStatus?.verification_url && (
            <a
              href={loginStatus.verification_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-content-primary-soft hover:underline"
            >
              Open the AWS verification page
            </a>
          )}
        </VStack>
      ) : aws.available_profiles.length > 0 && !showAddProfile ? (
        <VStack className="gap-3 items-stretch">
          <VStack className="gap-1 items-start">
            <Text level="label-small" className="text-content-layout-1">
              Sign into AWS
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              Sign in with AWS SSO so we can read details about your imported
              databases.
            </Text>
          </VStack>
          <div className="w-full max-w-96">
            <Text
              level="caption"
              className="text-content-layout-3 mb-1.5 block"
            >
              Profile
            </Text>
            <BaseInputSelect
              name="fleet-discover-profile"
              value={selectedProfile || aws.available_profiles[0]}
              onValueChange={setProfile}
              options={aws.available_profiles.map((name) => ({
                value: name,
                label: name,
              }))}
            />
          </div>
          <HStack className="gap-2 items-center">
            <Button
              variant="primary"
              modifier="solid"
              label={starting ? 'Opening browser…' : 'Sign in with AWS SSO'}
              disabled={starting}
              onClick={() =>
                void beginLogin(selectedProfile || aws.available_profiles[0])
              }
            />
            <button
              type="button"
              onClick={() => setShowAddProfile(true)}
              className="cursor-pointer"
            >
              <Text
                level="caption"
                className="text-content-layout-3 hover:underline"
              >
                Add another profile
              </Text>
            </button>
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label={isFetching ? 'Checking…' : 'Check again'}
              disabled={isFetching}
              onClick={() => void refetch()}
            />
          </HStack>
        </VStack>
      ) : (
        <AwsSsoWizard
          canCancel={aws.available_profiles.length > 0}
          onCancel={() => setShowAddProfile(false)}
          onConnected={async (createdProfile) => {
            setShowAddProfile(false)
            setProfile(createdProfile)
            await refetch()
          }}
          defaultRegion={aws.region}
        />
      )}
      {loginError && (
        <AwsErrorBlock
          error={loginError}
          onRetry={() => {
            setLoginError(null)
            void beginLogin(selectedProfile || aws.available_profiles[0])
          }}
        />
      )}
    </VStack>
  )
}
