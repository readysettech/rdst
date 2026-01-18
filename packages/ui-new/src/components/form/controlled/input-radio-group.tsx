import { Controller, type FieldValues } from 'react-hook-form'
import { Field } from '../_parts/field'
import {
  BaseInputRadioGroup,
  type BaseInputRadioGroupProps,
} from '../base/input-radio-group'
import type { InputControlledProps } from './type'

type InputRadioGroupProps<T extends FieldValues> = InputControlledProps<T> &
  BaseInputRadioGroupProps

export const InputRadioGroup = <T extends FieldValues>({
  control,
  name,
  label,
  info,
  required,
  options,
  containerProps,
  ...props
}: InputRadioGroupProps<T>) => (
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
          <BaseInputRadioGroup
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
