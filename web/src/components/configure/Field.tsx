import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Text } from '@rs/ui-new/text'
import { useId } from 'react'

/** The add-database dialog's field: a caption label over a compact full-width input. */
export function Field({
  label,
  name,
  value,
  onChange,
  type = 'text',
  placeholder,
  helper,
  disabled,
  mono,
  className,
}: {
  label: string
  name: string
  value: string
  onChange: (value: string) => void
  type?: 'text' | 'password'
  placeholder?: string
  helper?: string
  disabled?: boolean
  mono?: boolean
  className?: string
}) {
  const id = useId()
  return (
    <div className={className}>
      <label
        htmlFor={id}
        className="mb-1 block text-caption text-content-layout-3"
      >
        {label}
      </label>
      <BaseInputText
        id={id}
        name={name}
        type={type}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete={type === 'password' ? 'new-password' : 'off'}
        onChange={(event) => onChange(event.target.value)}
        className={`h-8.5 bg-surface-layout-2 ${mono ? 'font-mono text-mono-small' : 'text-body-small'}`}
      />
      {helper && (
        <Text as="p" level="caption" className="mt-1 text-content-layout-3">
          {helper}
        </Text>
      )}
    </div>
  )
}
