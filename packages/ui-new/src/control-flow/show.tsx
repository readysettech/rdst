import type { ReactNode } from 'react'
import type { JSX } from 'react/jsx-runtime'

type ShowProps<T> = {
  when: T
  fallback?: JSX.Element
  children: ((value: NonNullable<T>) => ReactNode) | ReactNode
}

// recommended usage: <Show when={data} fallback={<Loading />}>{data => <Component data={data} />}</Show>
export const Show = <T,>({
  when,
  fallback,
  children,
}: ShowProps<T>): JSX.Element => {
  // biome-ignore lint/complexity/noUselessFragments: <!>
  const renderFallback = fallback || <></>
  const renderChildren =
    typeof children === 'function' && when
      ? children(when as NonNullable<T>)
      : children

  // biome-ignore lint/complexity/noUselessFragments: <!>
  return when ? <>{renderChildren}</> : renderFallback
}
