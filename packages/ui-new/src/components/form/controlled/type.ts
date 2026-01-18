import type { ComponentProps } from 'react'
import type { Control, FieldValues, Path } from 'react-hook-form'

export type InputControlledProps<T extends FieldValues> = {
  control: Control<T>
  name: Path<T>
  label: string
  info?: string
  required?: boolean
  containerProps?: Omit<ComponentProps<'div'>, 'children'>
}
