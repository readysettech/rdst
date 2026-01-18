'use client'

import { Controller, type FieldValues } from 'react-hook-form'
import { Field } from '../_parts/field'
import {
  BaseInputTextarea,
  type BaseInputTextareaProps,
} from '../base/input-textarea'
import type { InputControlledProps } from './type'

type InputTextareaProps<T extends FieldValues> = InputControlledProps<T> &
  BaseInputTextareaProps

export const InputTextarea = <T extends FieldValues>({
  control,
  name,
  label,
  info,
  required,
  containerProps,
  ...props
}: InputTextareaProps<T>) => (
  <Field.Root {...containerProps}>
    <Field.Label htmlFor={name} required={required}>
      {label}
    </Field.Label>
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState: { error } }) => (
        <Field.Content>
          <BaseInputTextarea
            id={name}
            error={!!error?.message}
            {...field}
            {...props}
          />
          <Field.Info errorMessage={error?.message} infoMessage={info} />
        </Field.Content>
      )}
    />
  </Field.Root>
)
