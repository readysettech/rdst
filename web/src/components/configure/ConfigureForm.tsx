// The parts the add and edit dialog shares: the Advanced disclosure (engine, SSH jump host,
// TLS) and the rules a database's name follows.
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Text } from '@rs/ui-new/text'
import { useId } from 'react'
import type { ConfigureFormData } from '../../types/configure'
import { Field } from './Field'
import {
  assembleSshConfig,
  SshFields,
  type SshFieldsValue,
  sshFieldsValue,
} from './SshFields'

export interface Advanced {
  /** Null reads the engine from the connection string or the port. */
  engine: string | null
  tls: boolean
  tlsVerify: boolean
  tlsCa: string
  ssh: SshFieldsValue
}

const ENGINES = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
]

const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export function advancedFrom(saved?: Partial<ConfigureFormData>): Advanced {
  return {
    engine: saved?.engine ?? null,
    tls: saved?.tls ?? false,
    tlsVerify: saved?.tls_verify ?? false,
    tlsCa: saved?.tls_ca ?? '',
    ssh: sshFieldsValue(saved?.ssh),
  }
}

/** The Advanced values as a connection carries them. */
export function advancedConnection(advanced: Advanced) {
  return {
    tls: advanced.tls,
    tls_verify: advanced.tls && advanced.tlsVerify,
    tls_ca: advanced.tlsCa.trim() || undefined,
    ssh: assembleSshConfig(advanced.ssh),
  }
}

/** Why a typed name can't be used, or null. `taken` holds the other databases' names. */
export function nameError(
  name: string,
  taken: readonly string[]
): string | null {
  const trimmed = name.trim()
  if (!trimmed) return null
  if (!NAME.test(trimmed) || trimmed.endsWith('.'))
    return 'Use up to 64 letters, numbers, dots, underscores or hyphens, starting with a letter or number.'
  const folded = trimmed.toLowerCase()
  if (taken.some((other) => other.toLowerCase() === folded))
    return `A database named ${trimmed} already exists.`
  return null
}

export function defaultPasswordEnv(targetName: string): string {
  const normalized = targetName
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return normalized ? `RDST_${normalized}_PASSWORD` : ''
}

function Toggle({
  label,
  name,
  checked,
  onChange,
  disabled,
}: {
  label: string
  name: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-center justify-between gap-3">
      <label htmlFor={id} className="text-body-small text-content-layout-2">
        {label}
      </label>
      <BaseInputSwitch
        id={id}
        name={name}
        aria-label={label}
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
      />
    </div>
  )
}

/** Engine, SSH jump host and TLS, collapsed until asked for. */
export function AdvancedFields({
  value,
  engine,
  onChange,
  disabled,
  defaultOpen,
}: {
  value: Advanced
  /** The engine in effect, shown when none was picked. */
  engine: string
  onChange: (next: Advanced) => void
  disabled?: boolean
  defaultOpen?: boolean
}) {
  const engineId = useId()
  const set = (patch: Partial<Advanced>) => onChange({ ...value, ...patch })
  return (
    <Disclosure
      title="Advanced"
      subtitle="Engine, SSH jump host, TLS"
      defaultOpen={defaultOpen}
      className="mt-4"
      panelClassName="space-y-4"
    >
      <div>
        <label
          htmlFor={engineId}
          className="mb-1 block text-caption text-content-layout-3"
        >
          Engine
        </label>
        <BaseInputSelect
          id={engineId}
          name="engine"
          options={ENGINES}
          value={engine}
          onValueChange={(next) => set({ engine: next })}
          disabled={disabled}
        />
      </div>
      <div>
        <Text as="p" level="label-small" className="mb-2 text-content-layout-1">
          SSH jump host
        </Text>
        <SshFields
          value={value.ssh}
          onChange={(ssh) => set({ ssh })}
          disabled={disabled}
          idPrefix="add-ssh"
        />
      </div>
      <div className="space-y-3">
        <Toggle
          label="TLS encryption"
          name="tls"
          checked={value.tls}
          onChange={(tls) => set(tls ? { tls } : { tls, tlsVerify: false })}
          disabled={disabled}
        />
        {value.tls && (
          <Toggle
            label="Verify TLS certificate"
            name="tls_verify"
            checked={value.tlsVerify}
            onChange={(tlsVerify) => set({ tlsVerify })}
            disabled={disabled}
          />
        )}
        {value.tls && value.tlsVerify && (
          <Field
            label="TLS CA path (optional)"
            name="tls_ca"
            value={value.tlsCa}
            onChange={(tlsCa) => set({ tlsCa })}
            placeholder="/path/to/ca-certificate.pem"
            disabled={disabled}
          />
        )}
      </div>
    </Disclosure>
  )
}
