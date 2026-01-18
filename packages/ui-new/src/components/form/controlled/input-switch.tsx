'use client'

import { cn } from '@rs/tailwind-base'
import type { ReactNode } from 'react'
import { Controller, type FieldValues } from 'react-hook-form'
import { HStack } from '../../element/stack'
import { Field } from '../_parts/field'
import {
  BaseInputSwitch,
  type BaseInputSwitchProps,
} from '../base/input-switch'
import type { InputControlledProps } from './type'

type InputSwitchProps<T extends FieldValues> = {
  description?: string
  afterLabel?: ReactNode
} & InputControlledProps<T> &
  Pick<BaseInputSwitchProps, 'disabled' | 'id'>

export const InputSwitch = <T extends FieldValues>({
  control,
  name,
  label,
  info,
  required,
  description,
  containerProps,
  afterLabel,
  ...props
}: InputSwitchProps<T>) => (
  <Field.Root
    {...containerProps}
    className={cn('grid-cols-[1fr_auto]', containerProps?.className)}
  >
    <HStack>
      <Field.Label htmlFor={name} required={required}>
        {label}
      </Field.Label>
      {afterLabel}
    </HStack>
    <Controller
      control={control}
      name={name}
      render={({ field: { value, onChange, ...restField } }) => (
        <Field.Content>
          <BaseInputSwitch
            id={name}
            checked={value}
            onCheckedChange={onChange}
            {...restField}
            {...props}
          />
        </Field.Content>
      )}
    />
    <Field.Info infoMessage={description} />
  </Field.Root>
)
