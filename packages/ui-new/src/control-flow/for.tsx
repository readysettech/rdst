import React, { type JSX, type Key, useMemo } from 'react'

type ForProps<T> = {
  each: readonly T[] | null | undefined
  keyExtractor: (data: NonNullable<T>, index: number) => Key
  children: (
    value: NonNullable<T>,
    index: number,
    array: NonNullable<T>[]
  ) => JSX.Element
}

// recommended usage: <For each={data} keyExtractor={item => item.id}>{item => <Component item={item} />}</For>
export const For = <T,>({
  each,
  keyExtractor,
  children,
}: ForProps<T>): JSX.Element => {
  const array = useMemo(
    () =>
      (each ?? []).filter(
        (v): v is NonNullable<T> => typeof v !== 'undefined' && v !== null
      ),
    [each]
  )

  return (
    <>
      {array.map((v, i, a) => (
        <React.Fragment key={keyExtractor(v, i)}>
          {children(v, i, a)}
        </React.Fragment>
      ))}
    </>
  )
}
