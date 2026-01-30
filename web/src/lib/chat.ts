import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { useCallback, useState } from 'react';
import type { ChangeEvent } from 'react';

export interface ConversationStatus {
  exists: boolean;
  messageCount: number;
  totalExchanges: number;
  lastUpdated: string | null;
}

export function useInteractiveChat(queryHash: string, analysisResults?: any) {
  // Local input state (AI SDK 5.0 doesn't manage input internally)
  const [input, setInput] = useState('');
  const [conversationStatus, setConversationStatus] = useState<ConversationStatus | null>(null);
  
  const { 
    messages,     // UIMessage[] with parts array
    status,       // 'ready' | 'submitted' | 'streaming' | 'error'
    error, 
    sendMessage,  // async function to send message
    setMessages,
    stop 
  } = useChat({
    transport: new DefaultChatTransport({
      api: `/api/interactive/${queryHash}/message`,
      // Transform to backend's expected format
      prepareSendMessagesRequest: ({ messages }) => {
        const lastUserMessage = messages.filter(m => m.role === 'user').pop();
        // Extract text from parts (AI SDK 5.0 message shape)
        const textPart = lastUserMessage?.parts?.find(p => p.type === 'text');
        const messageText = textPart?.text || '';
        return {
          body: {
            message: messageText,
            analysis_results: analysisResults,
            continue_existing: true,
          },
        };
      },
    }),
  });

  // Derived state
  const isLoading = status === 'submitted' || status === 'streaming';
  
  // Handle input change (managed locally in AI SDK 5.0)
  const handleInputChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
  }, []);
  
  // Handle submit (no event parameter - called directly by MessageInput)
  const handleSubmit = useCallback(async () => {
    if (!input.trim()) return;
    await sendMessage({ text: input });
    setInput('');
  }, [input, sendMessage]);

  // Check conversation status (exists, message count, etc.)
  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch(`/api/interactive/${queryHash}/status`);
      if (!res.ok) {
        setConversationStatus({ exists: false, messageCount: 0, totalExchanges: 0, lastUpdated: null });
        return;
      }
      const data = await res.json();
      setConversationStatus({
        exists: data.exists,
        messageCount: data.message_count || 0,
        totalExchanges: data.total_exchanges || 0,
        lastUpdated: data.last_updated || null,
      });
    } catch (err) {
      console.error('Failed to check status:', err);
      setConversationStatus({ exists: false, messageCount: 0, totalExchanges: 0, lastUpdated: null });
    }
  }, [queryHash]);

  // Load existing conversation history
  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch(`/api/interactive/${queryHash}/history`);
      if (!res.ok) return;
      const data = await res.json();
      // Convert backend messages to AI SDK 5.0 UIMessage format
      const formattedMessages = data.messages.map((msg: any, i: number) => ({
        id: `msg-${i}`,
        role: msg.role as 'user' | 'assistant',
        parts: [{ type: 'text' as const, text: msg.content }],
      }));
      setMessages(formattedMessages);
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }, [queryHash, setMessages]);

  // Clear conversation and start fresh
  const clearConversation = useCallback(async () => {
    try {
      const res = await fetch(`/api/interactive/${queryHash}`, { method: 'DELETE' });
      if (res.ok) {
        setMessages([]);
        setConversationStatus({ exists: false, messageCount: 0, totalExchanges: 0, lastUpdated: null });
      }
    } catch (err) {
      console.error('Failed to clear conversation:', err);
    }
  }, [queryHash, setMessages]);

  return {
    messages,        // UIMessage[] with parts
    input,           // string (locally managed)
    handleInputChange,
    handleSubmit,
    isLoading,       // boolean derived from status
    error,
    loadHistory,
    checkStatus,
    clearConversation,
    conversationStatus,
    stop,
  };
}

// Helper to extract text content from UIMessage
export function getMessageContent(message: { parts?: Array<{ type: string; text?: string }> }): string {
  const textPart = message.parts?.find(p => p.type === 'text');
  return textPart?.text || '';
}
