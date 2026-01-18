import { tv } from '@rs/tailwind-base'
import type { ComponentProps } from 'react'
import { Text } from '../../element/text'
import { Label, type LabelProps } from './label'

const rootStyles = tv({
  base: ['grid', 'gap-2', 'px-6', 'py-6', 'items-start', 'w-full'],
})

const contentStyles = tv({
  base: ['grid', 'gap-2'],
})

const labelStyles = tv({
  base: ['pt-0'],
})

export type FieldRootProps = ComponentProps<'div'>

const Root = ({ className, children, ...props }: FieldRootProps) => (
  <div className="@container">
    <div className={rootStyles({ class: className })} {...props}>
      {children}
    </div>
  </div>
)

const Content = ({ className, children, ...props }: ComponentProps<'div'>) => (
  <div className={contentStyles({ class: className })} {...props}>
    {children}
  </div>
)

const FieldLabel = ({ children, className, ...props }: LabelProps) => {
  if (!children) return null

  return (
    <Label className={labelStyles({ class: className })} {...props}>
      {children}
    </Label>
  )
}

type InputLayoutInfoProps = {
  errorMessage?: string
  infoMessage?: string
}

const Info = ({ errorMessage, infoMessage }: InputLayoutInfoProps) => {
  if (!(errorMessage || infoMessage)) return null

  return (
    <Text
      level="caption"
      className={
        errorMessage ? 'text-content-negative-plain' : 'text-content-layout-3'
      }
    >
      {errorMessage ?? infoMessage}
    </Text>
  )
}

type FieldType = {
  Root: typeof Root
  Label: typeof FieldLabel
  Content: typeof Content
  Info: typeof Info
}

export const Field: FieldType = {
  Root,
  Label: FieldLabel,
  Content,
  Info,
}
