'use client'

import { Controller, type FieldValues } from 'react-hook-form'
import { Field } from '../_parts/field'
import { BaseInputText, type BaseInputTextProps } from '../base/input-text'
import type { InputControlledProps } from './type'

type InputTextProps<T extends FieldValues> = InputControlledProps<T> &
  BaseInputTextProps

export const InputText = <T extends FieldValues>({
  control,
  name,
  label,
  info,
  required,
  containerProps,
  ...props
}: InputTextProps<T>) => (
  <Field.Root {...containerProps}>
    <Field.Label htmlFor={name} required={required}>
      {label}
    </Field.Label>
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState: { error } }) => (
        <Field.Content>
          <BaseInputText
            id={name}
            error={!!error?.message}
            {...field}
            {...props}
            onChange={(e) => {
              if (props.type === 'number') {
                const v = (e.target as HTMLInputElement).value
                const next = v === '' ? undefined : Number(v)
                field.onChange(next)
              } else {
                field.onChange(e)
              }
              props.onChange?.(e)
            }}
          />
          <Field.Info errorMessage={error?.message} infoMessage={info} />
        </Field.Content>
      )}
    />
  </Field.Root>
)
