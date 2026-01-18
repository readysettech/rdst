import {
  Children,
  type FC,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from 'react'

interface SwitchProps<T> {
  value: T
  children: ReactNode
}

interface CaseProps<T> {
  value: T | ((currentValue: T) => boolean)
  children: ReactNode
}

interface DefaultCaseProps {
  children: ReactNode
}

export function Case<T>(props: CaseProps<T>): ReactElement<CaseProps<T>> {
  return <>{props.children}</>
}

export const DefaultCase: FC<DefaultCaseProps> = ({ children }) => (
  <>{children}</>
)

export function Switch<T>({
  value,
  children,
}: SwitchProps<T>): ReactElement | null {
  if (!children) return null

  const matchingCases: ReactElement[] = []
  const defaultMatches: ReactElement[] = []

  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return

    if (child.type === Case) {
      const caseElement = child as ReactElement<CaseProps<T>>
      const { value: caseValue } = caseElement.props
      const isMatch =
        typeof caseValue === 'function'
          ? (caseValue as (v: T) => boolean)(value)
          : caseValue === value
      if (isMatch) {
        matchingCases.push(caseElement)
      }
    } else if (child.type === DefaultCase) {
      defaultMatches.push(child)
    }
  })

  // biome-ignore lint/complexity/noUselessFragments: <!>
  return matchingCases.length > 0 ? <>{matchingCases}</> : <>{defaultMatches}</>
}
