import prism from 'prismjs'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-sql'
import { cn } from '@rs/tailwind-base'
import { useEffect, useRef } from 'react'
import './highlight.css'
import type { WithChildren } from '../../helpers/types'
import { Scrollable } from '../overlay/scrollable'

export type HighlightLanguageType =
  | 'json'
  | 'sql'
  | 'bash'
  | 'markdown'
  | 'js'
  | 'python'

type HighlightProps = {
  lang?: HighlightLanguageType
  isMarketing?: boolean
} & WithChildren

export const Highlight = ({
  lang = 'json',
  children,
  isMarketing,
}: HighlightProps) => {
  const codeRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (typeof window !== 'undefined' && codeRef.current) {
      prism.highlightElement(codeRef.current)
    }
  }, [lang, children])

  const className = cn('rs-highlight', isMarketing && 'rs-highlight--marketing')

  if (isMarketing) {
    return (
      <pre className={className}>
        <Scrollable orientation="horizontal" className="p-6">
          <code ref={codeRef} className={`language-${lang}`}>
            {children}
          </code>
        </Scrollable>
      </pre>
    )
  }

  return (
    <pre className={className}>
      <code ref={codeRef} className={`language-${lang}`}>
        {children}
      </code>
    </pre>
  )
}
