import { cleanup, render, renderHook, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { AiGate } from '../lib/useAiGate'
import {
  AnalysisConversation,
  type AnalysisConversationState,
  useAnalysisConversation,
} from './AnalysisConversation'

const mocks = vi.hoisted(() => ({
  gate: { current: { status: 'ready' } as AiGate },
  chat: {
    checkStatus: vi.fn(),
    loadHistory: vi.fn(),
  },
  useInteractiveChat: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}))
vi.mock('../lib/useAiGate', () => ({ useAiGate: () => mocks.gate.current }))
vi.mock('../lib/chat', () => ({
  useInteractiveChat: (hash: string, context?: unknown) => {
    mocks.useInteractiveChat(hash, context)
    return {
      messages: [],
      input: '',
      handleInputChange: vi.fn(),
      handleSubmit: vi.fn(),
      isLoading: false,
      error: undefined,
      loadHistory: mocks.chat.loadHistory,
      checkStatus: mocks.chat.checkStatus,
      clearConversation: vi.fn(),
      conversationStatus: { exists: true, messageCount: 4, totalExchanges: 2 },
    }
  },
  getMessageContent: () => '',
}))

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  mocks.gate.current = { status: 'ready' }
  mocks.chat.checkStatus.mockClear()
  mocks.chat.loadHistory.mockClear()
  mocks.useInteractiveChat.mockClear()
})

const context = {
  analysis_id: 'a5',
  target: 'demo',
  query_sql: 'SELECT 1',
}

describe('useAnalysisConversation', () => {
  it('keys the thread on the analyzed query and reads it back once showing', () => {
    const { result } = renderHook(() => useAnalysisConversation('qh1', context))

    expect(mocks.useInteractiveChat).toHaveBeenCalledWith('qh1', context)
    expect(mocks.chat.checkStatus).toHaveBeenCalled()
    expect(mocks.chat.loadHistory).toHaveBeenCalled()
    expect(result.current.hasPreviousChat).toBe(true)
  })

  it('reads nothing back while the surface is closed', () => {
    renderHook(() => useAnalysisConversation('qh1', context, false))

    expect(mocks.chat.loadHistory).not.toHaveBeenCalled()
    expect(mocks.chat.checkStatus).not.toHaveBeenCalled()
  })
})

function conversation(): AnalysisConversationState {
  return {
    messages: [],
    input: '',
    handleInputChange: vi.fn(),
    handleSubmit: vi.fn(),
    isLoading: false,
    error: undefined,
    hasPreviousChat: true,
  } as unknown as AnalysisConversationState
}

describe('AnalysisConversation', () => {
  it('composes a message when the AI dependency is satisfied', () => {
    render(<AnalysisConversation conversation={conversation()} />)

    expect(screen.getByPlaceholderText('Ask a question...')).toBeTruthy()
    expect(screen.queryByTestId('conversation-ai-key-note')).toBeNull()
  })

  it('names the missing key instead of offering an input that cannot send', () => {
    mocks.gate.current = { status: 'blocked', reason: 'missing' }
    render(<AnalysisConversation conversation={conversation()} />)

    expect(screen.getByTestId('conversation-ai-key-note')).toBeTruthy()
    expect(screen.queryByPlaceholderText('Ask a question...')).toBeNull()
  })

  it('says when the credits, rather than the key, are what ran out', () => {
    mocks.gate.current = { status: 'blocked', reason: 'exhausted' }
    render(<AnalysisConversation conversation={conversation()} />)

    expect(
      screen.getByText(/Free AI credits are used up/).textContent
    ).toBeTruthy()
  })
})
