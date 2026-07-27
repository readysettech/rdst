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
  createFleetAwsProfile,
  FleetAwsLoginError,
  type FleetAwsLoginStatus,
  type FleetAwsProfileInput,
  fetchFleetAwsLogin,
  fetchFleetAwsStatus,
  fleetAwsLogout,
  startFleetAwsLogin,
} from '../../lib/useFleet'

export const awsIdentityLabel = (arn: string | null): string =>
  arn ? (arn.split('/').pop() ?? arn) : 'unknown identity'

const EMPTY_PROFILE: FleetAwsProfileInput = {
  name: '',
  sso_start_url: '',
  sso_region: '',
  sso_account_id: '',
  sso_role_name: '',
  region: '',
}

function Field({
  label,
  name,
  value,
  placeholder,
  onChange,
}: {
  label: string
  name: string
  value: string
  placeholder?: string
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
    </div>
  )
}

export function AwsConnectionPanel({
  enabled = true,
  profile,
  onProfileChange,
  onRegionPrefill,
  compact = false,
}: {
  enabled?: boolean
  profile?: string
  onProfileChange?: (profile: string) => void
  onRegionPrefill?: (region: string) => void
  compact?: boolean
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
  const [profileForm, setProfileForm] = useState(EMPTY_PROFILE)
  const [creating, setCreating] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const [showAddProfile, setShowAddProfile] = useState(false)

  const {
    data: aws,
    isLoading,
    isError,
    error,
    refetch,
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

  const createProfile = async () => {
    setCreating(true)
    setLoginError(null)
    try {
      const created = await createFleetAwsProfile(profileForm)
      setProfile(created.profile)
      await refetch()
      await beginLogin(created.profile)
    } catch (caught) {
      setLoginError(
        caught instanceof FleetAwsLoginError
          ? caught
          : new FleetAwsLoginError(String(caught))
      )
    } finally {
      setCreating(false)
    }
  }

  if (!enabled) return null
  if (isLoading)
    return (
      <HStack className="gap-2 items-center" data-testid="aws-connection-panel">
        <Spinner size="base" />
        <Text level="caption" className="text-content-layout-3">
          Checking AWS credentials…
        </Text>
      </HStack>
    )
  if (isError)
    return (
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

  if (aws.has_credentials) {
    const content = (
      <VStack className="gap-3 items-stretch">
        <HStack className="gap-2 items-center flex-wrap">
          <Icon
            name="tick-double"
            label="Signed in"
            className="w-4 h-4 text-content-positive-soft shrink-0"
          />
          <Text level="caption" className="text-content-positive-soft">
            Signed into AWS as {awsIdentityLabel(aws.identity_arn)}
          </Text>
          <button
            type="button"
            disabled={signingOut}
            onClick={() => {
              setSigningOut(true)
              void fleetAwsLogout()
                .catch(() => {})
                .then(() => refetch())
                .finally(() => setSigningOut(false))
            }}
            className="cursor-pointer inline-flex items-center gap-1 disabled:opacity-60"
          >
            {signingOut && <Spinner size="base" />}
            <Text
              level="caption"
              className="text-content-layout-3 hover:underline"
            >
              {signingOut ? 'Signing out…' : 'Sign out of AWS'}
            </Text>
          </button>
        </HStack>
        {aws.available_profiles.length > 0 && (
          <div className="w-full max-w-96 pt-1">
            <Text
              level="caption"
              className="text-content-layout-3 mb-1.5 block"
            >
              Profile
            </Text>
            <BaseInputSelect
              name="fleet-discover-profile"
              value={selectedProfile}
              onValueChange={setProfile}
              options={aws.available_profiles.map((name) => ({
                value: name,
                label: name,
              }))}
            />
          </div>
        )}
      </VStack>
    )
    return compact ? (
      <div
        className="rounded-xl border border-border-layout-1 bg-surface-layout-1 px-4 py-3"
        data-testid="aws-connection-panel"
      >
        {content}
      </div>
    ) : (
      <div data-testid="aws-connection-panel">{content}</div>
    )
  }

  const fallback = loginError?.fallbackCommand
  const createProfileForm = (
    <VStack className="gap-3 items-stretch">
      <VStack className="gap-1 items-start">
        <Text level="label-small" className="text-content-layout-1">
          Add an AWS SSO profile
        </Text>
        <Text level="body-small" className="text-content-layout-2">
          Create a local SSO profile, then we’ll open the browser sign-in for
          you. Needs AWS CLI v2 installed.
        </Text>
      </VStack>
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
        <Field
          label="Profile name"
          name="aws-profile-name"
          value={profileForm.name}
          placeholder="dev"
          onChange={(name) => setProfileForm((v) => ({ ...v, name }))}
        />
        <Field
          label="Start URL"
          name="aws-sso-start-url"
          value={profileForm.sso_start_url}
          placeholder="https://example.awsapps.com/start"
          onChange={(sso_start_url) =>
            setProfileForm((v) => ({ ...v, sso_start_url }))
          }
        />
        <Field
          label="SSO region"
          name="aws-sso-region"
          value={profileForm.sso_region}
          placeholder="us-east-1"
          onChange={(sso_region) =>
            setProfileForm((v) => ({ ...v, sso_region }))
          }
        />
        <Field
          label="Account ID"
          name="aws-sso-account-id"
          value={profileForm.sso_account_id}
          onChange={(sso_account_id) =>
            setProfileForm((v) => ({ ...v, sso_account_id }))
          }
        />
        <Field
          label="Role"
          name="aws-sso-role-name"
          value={profileForm.sso_role_name}
          placeholder="Developer"
          onChange={(sso_role_name) =>
            setProfileForm((v) => ({ ...v, sso_role_name }))
          }
        />
        <Field
          label="Default region"
          name="aws-default-region"
          value={profileForm.region}
          placeholder="us-east-1"
          onChange={(region) => setProfileForm((v) => ({ ...v, region }))}
        />
      </div>
      <HStack className="gap-2">
        <Button
          variant="primary"
          modifier="solid"
          label={creating ? 'Creating…' : 'Create profile and sign in'}
          disabled={
            creating ||
            Object.values(profileForm).some((value) => !value.trim())
          }
          onClick={() => void createProfile()}
        />
        {aws.available_profiles.length > 0 && (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Cancel"
            onClick={() => setShowAddProfile(false)}
          />
        )}
      </HStack>
    </VStack>
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
          </HStack>
        </VStack>
      ) : (
        createProfileForm
      )}
      {loginError && (
        <div className="rounded-lg border border-border-negative-soft bg-surface-negative-soft/20 p-3">
          <VStack className="gap-2 items-stretch">
            <Text level="label-small" className="text-content-negative-soft">
              {loginError.code === 'aws_cli_missing'
                ? 'AWS CLI is not installed'
                : 'AWS sign-in failed'}
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              {loginError.code === 'aws_cli_missing'
                ? 'Install AWS CLI v2, then try again.'
                : loginError.message}
            </Text>
            {fallback && (
              <HStack className="gap-2 items-center rounded bg-surface-layout-2 px-3 py-2">
                <code className="text-xs text-content-layout-2 break-all flex-1">
                  {fallback}
                </code>
                <CopyButton text={fallback} />
              </HStack>
            )}
            <HStack>
              <Button
                variant="primary"
                modifier="outline"
                size="small"
                label="Try again"
                onClick={() => {
                  setLoginError(null)
                  void beginLogin(
                    selectedProfile ||
                      aws.available_profiles[0] ||
                      profileForm.name
                  )
                }}
              />
            </HStack>
          </VStack>
        </div>
      )}
    </VStack>
  )
}
