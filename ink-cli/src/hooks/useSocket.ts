import { useState, useEffect, useCallback, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import type { Message, ToolCall, AppConfig } from '../types.js';

const BACKEND_URL = 'http://localhost:8000';

interface UseSocketReturn {
  connected: boolean;
  messages: Message[];
  streaming: boolean;
  config: AppConfig;
  workers: Array<{ id: string; role: string; status: string }>;
  sendMessage: (text: string, threadId: string) => void;
  stopGeneration: (threadId: string) => void;
  clearMessages: () => void;
  updateToolExpanded: (msgId: string, toolId: string, expanded: boolean) => void;
  updateConfig: (partial: Partial<AppConfig>) => void;
  /** Inject a local assistant message without calling AI */
  injectMessage: (content: string) => void;
  /** Raw socket for use by sub-hooks (e.g. useIntel) */
  socket: Socket | null;
}

export function useSocket(serverReady: boolean): UseSocketReturn {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [config, setConfig] = useState<AppConfig>({ provider: 'ollama', model: '...' });
  const [workers, setWorkers] = useState<Array<{ id: string; role: string; status: string }>>([]);
  const socketRef = useRef<Socket | null>(null);
  const currentMsgIdRef = useRef<string | null>(null);
  const streamBufferRef = useRef('');

  useEffect(() => {
    if (!serverReady) return;

    const socket = io(BACKEND_URL, {
      transports: ['websocket'],
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });
    socketRef.current = socket;

    socket.on('connect', () => {
      setConnected(true);
      // Fetch model info
      fetch(`${BACKEND_URL}/api/model`)
        .then(r => r.json())
        .then((d: AppConfig) => setConfig(d))
        .catch(() => {});
    });

    socket.on('disconnect', () => setConnected(false));

    socket.on('connected', () => {
      // initial handshake
    });

    socket.on('text', (data: { content: string }) => {
      streamBufferRef.current += data.content;
      const buf = streamBufferRef.current;
      if (currentMsgIdRef.current) {
        const id = currentMsgIdRef.current;
        setMessages(prev => prev.map(m =>
          m.id === id ? { ...m, content: buf, streaming: true } : m
        ));
      }
    });

    socket.on('tool:start', (data: { tool_id: string; tool_name: string; arguments?: Record<string, unknown> }) => {
      const tool: ToolCall = {
        tool_id: data.tool_id,
        tool_name: data.tool_name,
        status: 'running',
        input: data.arguments,
        expanded: false,
      };
      if (currentMsgIdRef.current) {
        const id = currentMsgIdRef.current;
        setMessages(prev => prev.map(m =>
          m.id === id ? { ...m, tools: [...m.tools, tool] } : m
        ));
      }
    });

    socket.on('tool:end', (data: { tool_id: string; tool_name: string; result?: string; elapsed_ms?: number }) => {
      if (currentMsgIdRef.current) {
        const id = currentMsgIdRef.current;
        setMessages(prev => prev.map(m =>
          m.id === id
            ? {
                ...m,
                tools: m.tools.map(t =>
                  t.tool_id === data.tool_id
                    ? { ...t, status: 'completed' as const, output: data.result, elapsed_ms: data.elapsed_ms }
                    : t
                ),
              }
            : m
        ));
      }
    });

    socket.on('done', () => {
      setStreaming(false);
      if (currentMsgIdRef.current) {
        const id = currentMsgIdRef.current;
        setMessages(prev => prev.map(m =>
          m.id === id ? { ...m, streaming: false } : m
        ));
        currentMsgIdRef.current = null;
        streamBufferRef.current = '';
      }
    });

    socket.on('error', (data: { content: string }) => {
      setStreaming(false);
      const errMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `⚠ Error: ${data.content}`,
        timestamp: new Date(),
        tools: [],
      };
      setMessages(prev => [...prev, errMsg]);
      currentMsgIdRef.current = null;
      streamBufferRef.current = '';
    });

    socket.on('team:workers', (data: { workers: Array<{ id: string; role: string; status: string }> }) => {
      setWorkers(data.workers ?? []);
    });

    return () => {
      socket.disconnect();
    };
  }, [serverReady]);

  const sendMessage = useCallback((text: string, threadId: string) => {
    const socket = socketRef.current;
    if (!socket || !socket.connected) return;

    // Add user message
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      timestamp: new Date(),
      tools: [],
    };

    // Prepare assistant message placeholder
    const assistantId = crypto.randomUUID();
    currentMsgIdRef.current = assistantId;
    streamBufferRef.current = '';

    const assistantMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      tools: [],
      streaming: true,
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setStreaming(true);

    socket.emit('chat:message', { message: text, thread_id: threadId, mode: 'code' });
  }, []);

  const stopGeneration = useCallback((threadId: string) => {
    socketRef.current?.emit('chat:stop', { thread_id: threadId });
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    currentMsgIdRef.current = null;
    streamBufferRef.current = '';
  }, []);

  const updateToolExpanded = useCallback((msgId: string, toolId: string, expanded: boolean) => {
    setMessages(prev => prev.map(m =>
      m.id === msgId
        ? { ...m, tools: m.tools.map(t => t.tool_id === toolId ? { ...t, expanded } : t) }
        : m
    ));
  }, []);

  const updateConfig = useCallback((partial: Partial<AppConfig>) => {
    setConfig(prev => ({ ...prev, ...partial }));
  }, []);

  const injectMessage = useCallback((content: string) => {
    const msg: Message = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content,
      timestamp: new Date(),
      tools: [],
    };
    setMessages(prev => [...prev, msg]);
  }, []);

  return { connected, messages, streaming, config, workers, sendMessage, stopGeneration, clearMessages, updateToolExpanded, updateConfig, injectMessage, socket: socketRef.current };
}
