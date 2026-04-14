import React, { useState, useCallback, useEffect } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import Spinner from 'ink-spinner';
import { useServer } from './hooks/useServer.js';
import { useSocket } from './hooks/useSocket.js';
import ChatPane from './components/ChatPane.js';
import Sidebar from './components/Sidebar.js';
import InputBox from './components/InputBox.js';
import StatusBar from './components/StatusBar.js';
import { theme } from './theme.js';
import type { FileNode } from './types.js';

const HELP_TEXT = `
◆ ShadowDev — Keyboard Shortcuts & Commands

  Ctrl+B        Toggle sidebar (file tree + team)
  Ctrl+L        Clear chat history
  Ctrl+N        New session
  Ctrl+C        Stop generation / Exit

  /help         Show this help
  /clear        Clear messages
  /new          New session
  /model [name] Show or switch model
  /provider [p] Show or switch provider
  /plan         Enter plan mode
  /doctor       Run diagnostics
  /config       View/set config
  /exit         Exit ShadowDev
`.trim();

export default function App() {
  const { exit } = useApp();
  const server = useServer();
  const socket = useSocket(server.isReady);

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const [files, setFiles] = useState<FileNode[]>([]);
  const [tokenCount, setTokenCount] = useState(0);

  // Fetch files when sidebar opens
  useEffect(() => {
    if (!sidebarOpen || !server.isReady) return;
    fetch('http://localhost:8000/api/files')
      .then(r => r.json())
      .then((data: { items?: Array<{ name: string; path: string; is_dir: boolean }> }) => {
        const nodes: FileNode[] = (data.items ?? []).map(item => ({
          name: item.name,
          path: item.path,
          type: item.is_dir ? 'directory' : 'file',
        }));
        setFiles(nodes);
      })
      .catch(() => {});
  }, [sidebarOpen, server.isReady]);

  // Track token count from messages (rough estimate)
  useEffect(() => {
    const totalChars = socket.messages.reduce((acc, m) => acc + m.content.length, 0);
    setTokenCount(Math.round(totalChars / 4));
  }, [socket.messages]);

  // Keyboard shortcuts
  useInput((input, key) => {
    if (key.ctrl) {
      if (input === 'b') setSidebarOpen(o => !o);
      if (input === 'l') socket.clearMessages();
      if (input === 'n') {
        setThreadId(crypto.randomUUID());
        socket.clearMessages();
      }
      if (input === 'c' && !socket.streaming) exit();
    }
  });

  const handleSubmit = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    // Slash command handling
    if (trimmed === '/clear') { socket.clearMessages(); return; }
    if (trimmed === '/exit' || trimmed === '/quit') { process.exit(0); }
    if (trimmed === '/new' || trimmed === '/reset') {
      setThreadId(crypto.randomUUID());
      socket.clearMessages();
      return;
    }
    if (trimmed === '/help') {
      // Inject help as assistant message — done via sendMessage so it shows in chat
      socket.sendMessage('/help', threadId);
      return;
    }

    socket.sendMessage(trimmed, threadId);
  }, [socket, threadId]);

  const handleCancel = useCallback(() => {
    socket.stopGeneration(threadId);
  }, [socket, threadId]);

  const handleToggleTool = useCallback((msgId: string, toolId: string) => {
    const msg = socket.messages.find(m => m.id === msgId);
    const tool = msg?.tools.find(t => t.tool_id === toolId);
    if (tool) socket.updateToolExpanded(msgId, toolId, !tool.expanded);
  }, [socket]);

  // ── Loading screen ──────────────────────────────────────────
  if (server.isStarting) {
    return (
      <Box flexDirection="column" alignItems="center" justifyContent="center" padding={2}>
        <Box gap={1}>
          <Text color={theme.accent}>
            <Spinner type="dots" />
          </Text>
          <Text color={theme.accent} bold>◆ ShadowDev</Text>
        </Box>
        <Text color={theme.textMuted}>Starting backend server...</Text>
        <Text color={theme.textDim}>python server/main.py</Text>
      </Box>
    );
  }

  // ── Error screen ──────────────────────────────────────────
  if (server.error) {
    return (
      <Box flexDirection="column" padding={2} gap={1}>
        <Text color={theme.red} bold>✗ Failed to start ShadowDev backend</Text>
        <Text color={theme.textMuted}>{server.error}</Text>
        <Text color={theme.textDim}>Make sure Python and dependencies are installed.</Text>
        <Text color={theme.textDim}>Try: pip install -r requirements.txt</Text>
      </Box>
    );
  }

  // ── Main UI ──────────────────────────────────────────────────
  return (
    <Box flexDirection="column" height={process.stdout.rows ?? 40}>
      {/* Header */}
      <Box
        borderStyle="single"
        borderColor={theme.border}
        paddingX={1}
        justifyContent="space-between"
      >
        <Box gap={1}>
          <Text color={theme.accent} bold>◆ ShadowDev</Text>
          <Text color={theme.textDim}>AI coding assistant</Text>
        </Box>
        <Box gap={2}>
          <Text color={theme.textDim}>Ctrl+B sidebar</Text>
          <Text color={theme.textDim}>Ctrl+L clear</Text>
          <Text color={theme.textDim}>Ctrl+N new</Text>
          <Text color={theme.textDim}>/help commands</Text>
        </Box>
      </Box>

      {/* Main content row */}
      <Box flexGrow={1} overflow="hidden">
        <Sidebar
          files={files}
          workers={socket.workers.map(w => ({ ...w, status: w.status as 'idle'|'running'|'done'|'error' }))}
          open={sidebarOpen}
        />
        <ChatPane
          messages={socket.messages}
          onToggleTool={handleToggleTool}
          sidebarOpen={sidebarOpen}
        />
      </Box>

      {/* Input */}
      <InputBox
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        disabled={false}
        streaming={socket.streaming}
      />

      {/* Status bar */}
      <StatusBar
        connected={socket.connected}
        config={socket.config}
        threadId={threadId}
        tokenCount={tokenCount}
        sidebarOpen={sidebarOpen}
        streaming={socket.streaming}
      />
    </Box>
  );
}
