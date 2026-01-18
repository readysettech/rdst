'use client'

import { Controller, type FieldValues } from 'react-hook-form'
import { Field } from '../_parts/field'
import {
  BaseInputSelect,
  type BaseInputSelectProps,
} from '../base/input-select'
import type { InputControlledProps } from './type'

type InputSelectProps<T extends FieldValues> = InputControlledProps<T> &
  Pick<BaseInputSelectProps, 'loading' | 'placeholder' | 'options' | 'disabled'>

export const InputSelect = <T extends FieldValues>({
  control,
  name,
  label,
  info,
  required,
  options,
  containerProps,
  ...props
}: InputSelectProps<T>) => (
  <Field.Root {...containerProps}>
    <Field.Label htmlFor={name} required={required}>
      {label}
    </Field.Label>
    <Controller
      control={control}
      name={name}
      render={({
        field: { value, onChange, ...restField },
        fieldState: { error },
      }) => (
        <Field.Content>
          <BaseInputSelect
            id={name}
            options={options}
            value={value}
            onValueChange={onChange}
            {...restField}
            {...props}
          />
          <Field.Info errorMessage={error?.message} infoMessage={info} />
        </Field.Content>
      )}
    />
  </Field.Root>
)
