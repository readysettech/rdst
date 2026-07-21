// Analyze page component — moved out of the route config into this route-ignored
// sibling (TanStack skips `-`-prefixed files) so the code-splitter can relocate
// its `../components` barrel import (which re-exports the CodeMirror SQL-editor
// stack) out of the eager entry chunk. Referencing an exported page as the
// route `component:` pins it (and its transitive CodeMirror imports) into the
// eager entry; the non-exported wrapper in `analyze.tsx` imports `AnalyzePage`
// only for its `component:`, and the tests import it from here. [FIX-1 / D-1]

import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { QueryEditor, QueryHistory, TargetLockNotice } from '../components'
import { useTarget } from '../hooks/useTarget'
import { useQueryRegistry } from '../lib/useQueryRegistry'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'

export function AnalyzePage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [fast, setFast] = useState(false)
  const { target: selectedTarget } = useTarget()
  const passwordLock = useTargetPasswordLock(selectedTarget)
  const { queries, addQuery } = useQueryRegistry()

  const handleAnalyze = useCallback(() => {
    if (passwordLock.isLocked) return
    if (!query.trim()) return
    addQuery(query, selectedTarget || undefined)
    navigate({
      to: '/results',
      search: {
        query: query.trim(),
        target: selectedTarget || undefined,
        fast: fast || undefined,
      },
    })
  }, [passwordLock.isLocked, query, selectedTarget, fast, addQuery, navigate])

  const editorRef = useRef<HTMLDivElement>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [editorFlash, setEditorFlash] = useState(false)

  // Selecting a recent/example card writes its SQL into the top editor. When the
  // user has scrolled down the list, that change is off-screen and the click
  // "feels broken" — so pair the write with cues the user can actually see: bring
  // the editor into view + focus it, plus a brief ring flash confirming the text
  // landed. block:"nearest" scrolls the minimum needed, so a click while the
  // editor is already visible causes no jarring jump; the smooth behaviour is
  // dropped under reduced-motion. [triage §1.5; USE-043 ≥2 cues, USE-008 apparent
  // effort, USE-071 confusion-reducing sizzle]
  const handleSelectHistory = useCallback((selectedQuery: string) => {
    setQuery(selectedQuery)
    const wrapper = editorRef.current
    if (wrapper) {
      const reduce =
        typeof window !== 'undefined' &&
        !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      wrapper.scrollIntoView({
        behavior: reduce ? 'auto' : 'smooth',
        block: 'nearest',
      })
      wrapper.querySelector<HTMLElement>('.cm-content')?.focus()
    }
    setEditorFlash(true)
    if (flashTimer.current) clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setEditorFlash(false), 700)
  }, [])

  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current)
    },
    []
  )

  return (
    <div className="space-y-8 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon
              name="querypilot"
              label="Analyze Query"
              className="w-6 h-6 text-content-primary-soft"
            />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Analyze Query
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Paste SQL to see how fast it runs and how to speed it up.
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {/* Query Editor */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        {passwordLock.isLocked && (
          <div className="mb-4">
            <TargetLockNotice
              message={passwordLock.message}
              requirements={passwordLock.missingTargetRequirements}
              keyringAvailable={passwordLock.keyringAvailable}
            />
          </div>
        )}
        {/* The ring flash is a box-shadow transition (no movement), so it reads
            the same under reduced-motion; the scroll behaviour is what we soften
            there. [USE-071] */}
        <div
          ref={editorRef}
          data-testid="analyze-editor"
          className={`rounded-2xl transition-shadow duration-500 ${
            editorFlash
              ? 'ring-2 ring-border-primary-soft ring-offset-2 ring-offset-surface-layout-1'
              : 'ring-0 ring-transparent'
          }`}
        >
          <QueryEditor
            value={query}
            onChange={setQuery}
            onAnalyze={handleAnalyze}
            disabled={passwordLock.isLocked}
            target={selectedTarget}
            fast={fast}
            onFastChange={setFast}
          />
        </div>
      </m.div>

      {/* Query History */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <QueryHistory queries={queries} onSelect={handleSelectHistory} />
      </m.div>
    </div>
  )
}
