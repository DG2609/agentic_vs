'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { getSocket } from '@/lib/socket';
import api from '@/lib/socket';
import type { FileItem, OpenTab, ChatMessage, ToolBlock, ModelInfo } from '@/lib/types';

// Components
import ActivityBar from '@/components/activity-bar';
import FileTree, { SearchPanel, ToolsPanel, GitPanel } from '@/components/file-tree';
import CodeEditor from '@/components/code-editor';
import ChatPanel from '@/components/chat-panel';
import StatusBar from '@/components/status-bar';

export default function IDEPage() {
  // ── State ──────────────────────────────────────────────────
  const [activePanel, setActivePanel] = useState<'explorer' | 'search' | 'tools' | 'git'>('explorer');
  const [chatOpen, setChatOpen] = useState(true);
  const [connected, setConnected] = useState(false);
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const [chatMode, setChatMode] = useState<'chat' | 'plan' | 'code'>('code');

  // File tree
  const [fileTree, setFileTree] = useState<FileItem[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Record<string, FileItem[]>>({});

  // Editor
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);

  // Chat
  const [messages, setMessages] = useState<ChatMessage[]>([{
    role: 'assistant',
    content: '👋 Hi! I have **27 tools** at my disposal — code analysis, web search, task tracking, planning, and more. Ask me anything!',
    timestamp: new Date(),
    tools: [],
  }]);
  const [inputValue, setInputValue] = useState('');
  const [activeTools, setActiveTools] = useState<Map<string, ToolBlock>>(new Map());
  const contentBufferRef = useRef('');

  // Custom force update for throttled streaming render
  const [, setTick] = useState(0);
  const rafRef = useRef<number | null>(null);

  // Search
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Array<{ file: string; line: number; content: string }>>([]);

  // ── Socket.IO Connection ───────────────────────────────────
  useEffect(() => {
    const socket = getSocket();

    socket.on('connect', () => setConnected(true));
    socket.on('text', (data: { content: string }) => {
      contentBufferRef.current += data.content;

      // Throttle rendering using requestAnimationFrame
      if (!rafRef.current) {
        rafRef.current = requestAnimationFrame(() => {
          setTick(t => t + 1); // Trigger a localized re-render
          rafRef.current = null;
        });
      }
    });

    socket.on('tool:start', (data: ToolBlock) => {
      setActiveTools((prev) => new Map(prev).set(data.tool_id, { ...data, status: 'running' }));
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === 'assistant') {
          // Prevent duplicate tools
          if (!last.tools.some((t) => t.tool_id === data.tool_id)) {
            updated[updated.length - 1] = {
              ...last,
              tools: [...last.tools, { ...data, status: 'running' }]
            };
          }
        }
        return updated;
      });
    });

    socket.on('tool:end', (data: ToolBlock) => {
      setActiveTools((prev) => new Map(prev).set(data.tool_id, data));
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === 'assistant') last.tools = last.tools.map((t) => t.tool_id === data.tool_id ? data : t);
        return updated;
      });
    });

    socket.on('done', () => {
      const finalContent = contentBufferRef.current; // Capture here
      setIsStreaming(false);
      setMessages((prev) => {
        const updated = [...prev];
        const lastIndex = updated.length - 1;
        if (lastIndex >= 0 && updated[lastIndex].role === 'assistant') {
          updated[lastIndex] = { ...updated[lastIndex], content: finalContent }; // Use local var
        }
        return updated;
      });
      contentBufferRef.current = '';
      setTick(t => t + 1); // Final update
      setTimeout(() => {
        setActiveTools((prev) => {
          const next = new Map(prev);
          for (const [id, t] of next) if (t.status === 'completed') next.delete(id);
          return next;
        });
      }, 3000);
    });

    socket.on('error', (data: { content: string }) => {
      setIsStreaming(false);
      setMessages((prev) => {
        const updated = [...prev];
        const lastIndex = updated.length - 1;
        if (lastIndex >= 0 && updated[lastIndex].role === 'assistant') {
          updated[lastIndex] = { ...updated[lastIndex], content: updated[lastIndex].content + `\n\n⚠️ Error: ${data.content}` };
        }
        return updated;
      });
    });

    socket.on('title', (data: { content: string }) => {
      document.title = `${data.content} — Agentic IDE`;
    });

    socket.on('file:diff', (data: { path: string; original: string; modified: string }) => {
      setTabs((prev) => {
        // If tab exists, update it to Diff mode. Otherwise, create it.
        const name = data.path.split('/').pop() || data.path;
        const existing = prev.find(t => t.path === data.path);

        const newTab = {
          path: data.path,
          name: name,
          content: data.modified,
          original: data.original,
          modified: false,
          isDiff: true,
          originalDiff: data.original
        };

        const updated = existing
          ? prev.map(t => t.path === data.path ? newTab : t)
          : [...prev, newTab];

        return updated;
      });
      setActiveTab(data.path);
    });

    return () => { socket.removeAllListeners(); };
  }, []);

  // ── File Operations ────────────────────────────────────────
  const loadRootFiles = useCallback(async () => {
    try {
      setFileTree((await api.listFiles('')).items || []);
    } catch { /* */ }
  }, []);

  // ── Init ───────────────────────────────────────────────────
  useEffect(() => {
    api.getModelInfo().then(setModelInfo).catch(() => { });
    loadRootFiles();
  }, [loadRootFiles]);

  const handleOpenFolder = useCallback(async () => {
    const path = window.prompt("Enter absolute path to workspace folder (e.g. C:\\MyProject or /Users/mac/my-app):", "");
    if (!path) return;
    try {
      const res = await api.setWorkspace(path);
      if (res.error) {
        alert(`Error: ${res.error}`);
        return;
      }
      // Expand everything, clear tabs, reload
      setExpandedDirs({});
      setTabs([]);
      setActiveTab(null);
      await loadRootFiles();
      setMessages((prev) => [...prev, {
        role: 'assistant',
        content: `📂 Workspace changed to \`${res.workspace}\`. Please wait a moment while I index the new files!`,
        timestamp: new Date(),
        tools: []
      }]);
    } catch (e) {
      alert("Failed to change workspace.");
    }
  }, [loadRootFiles]);

  const toggleDir = useCallback(async (path: string) => {
    if (expandedDirs[path]) {
      setExpandedDirs((prev) => { const n = { ...prev }; delete n[path]; return n; });
    } else {
      try { setExpandedDirs((prev) => ({ ...prev })); const d = await api.listFiles(path); setExpandedDirs((prev) => ({ ...prev, [path]: d.items || [] })); } catch { /* */ }
    }
  }, [expandedDirs]);

  const openFile = useCallback(async (path: string, name: string) => {
    if (tabs.find((t) => t.path === path)) { setActiveTab(path); return; }
    try {
      const d = await api.readFile(path);
      setTabs((prev) => [...prev, { path, name, content: d.content, original: d.content, modified: false }]);
      setActiveTab(path);
    } catch { /* */ }
  }, [tabs]);

  const closeTab = useCallback((path: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setTabs((prev) => {
      const filtered = prev.filter((t) => t.path !== path);
      if (activeTab === path) setActiveTab(filtered.length > 0 ? filtered[filtered.length - 1].path : null);
      return filtered;
    });
  }, [activeTab]);

  const onContentChange = useCallback((path: string, content: string) => {
    setTabs((prev) => prev.map((t) => t.path === path ? { ...t, content, modified: content !== t.original } : t));
  }, []);

  const saveFile = useCallback(async () => {
    const tab = tabs.find((t) => t.path === activeTab);
    if (!tab?.modified) return;
    try {
      await api.writeFile(tab.path, tab.content);
      setTabs((prev) => prev.map((t) => t.path === tab.path ? { ...t, original: t.content, modified: false } : t));
    } catch { /* */ }
  }, [tabs, activeTab]);

  const approveDiff = useCallback(async (path: string) => {
    // Diff is already written to disk by the backend tool, we just need to dismiss the diff view
    setTabs((prev) => prev.map((t) => {
      if (t.path === path) {
        return { ...t, isDiff: false, originalDiff: undefined, original: t.content, modified: false };
      }
      return t;
    }));
  }, []);

  const rejectDiff = useCallback(async (path: string) => {
    const tab = tabs.find(t => t.path === path);
    if (!tab || !tab.originalDiff) return;
    try {
      // Revert the file on disk using the backend API
      await fetch(`http://${window.location.hostname}:8000/api/file/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content: tab.originalDiff })
      });
      // Revert in frontend
      setTabs((prev) => prev.map((t) => {
        if (t.path === path) {
          return { ...t, isDiff: false, originalDiff: undefined, content: t.originalDiff!, original: t.originalDiff!, modified: false };
        }
        return t;
      }));
    } catch { /* */ }
  }, [tabs]);

  // ── Chat ───────────────────────────────────────────────────
  const sendMessage = useCallback(() => {
    const text = inputValue.trim();
    if (!text || isStreaming || !connected) return;
    setMessages((prev) => [...prev, { role: 'user', content: text, timestamp: new Date(), tools: [] }, { role: 'assistant', content: '', timestamp: new Date(), tools: [] }]);
    setInputValue('');
    setIsStreaming(true);
    contentBufferRef.current = '';
    setTick(t => t + 1);
    getSocket().emit('chat:message', { message: text, thread_id: threadId, mode: chatMode });
  }, [inputValue, isStreaming, connected, threadId, chatMode]);

  const stopGeneration = useCallback(() => {
    getSocket().emit('chat:stop', { thread_id: threadId });
  }, [threadId]);

  const newConversation = useCallback(() => {
    setThreadId(crypto.randomUUID());
    setMessages([{ role: 'assistant', content: '👋 New conversation started! How can I help?', timestamp: new Date(), tools: [] }]);
    document.title = 'Agentic IDE';
  }, []);

  const handleApplyCode = useCallback((code: string) => {
    if (!activeTab) {
      alert("Please open a file in the editor first before applying code.");
      return;
    }

    const message = `Please apply the following code exactly to \`${activeTab}\` using the \`file_edit\` tool. You may need to read the file first to find the exact \`old_string\` to replace. If it is a new file, use \`file_write\`.\n\n\`\`\`\n${code}\n\`\`\``;

    // Send to agent to intelligently apply it
    setMessages((prev) => [...prev, { role: 'user', content: `*(Action: Apply code to ${activeTab.split('/').pop()})*`, timestamp: new Date(), tools: [] }, { role: 'assistant', content: '', timestamp: new Date(), tools: [] }]);
    setIsStreaming(true);
    contentBufferRef.current = '';
    setTick(t => t + 1);
    getSocket().emit('chat:message', { message, thread_id: threadId, mode: 'code' });
  }, [activeTab, threadId]);

  // ── Search ─────────────────────────────────────────────────
  const doSearch = useCallback(async () => {
    if (!searchQuery.trim()) return;
    try { setSearchResults((await api.searchFiles(searchQuery)).results || []); } catch { /* */ }
  }, [searchQuery]);

  // ── Keyboard Shortcuts ─────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 's') { e.preventDefault(); saveFile(); }
      if (e.ctrlKey && e.shiftKey && e.key === 'I') { e.preventDefault(); setChatOpen((v) => !v); }
      if (e.ctrlKey && e.key === 'n') { e.preventDefault(); newConversation(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [saveFile, newConversation]);

  // ── Render ─────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-screen w-screen">
      <div className="flex flex-1 min-h-0">
        <ActivityBar
          activePanel={activePanel} onPanelChange={setActivePanel}
          chatOpen={chatOpen} onToggleChat={() => setChatOpen(!chatOpen)} />

        {/* Sidebar */}
        <div className="w-60 flex-shrink-0 flex flex-col overflow-hidden"
          style={{ background: 'var(--bg-panel)', borderRight: '1px solid var(--border)' }}>
          {activePanel === 'explorer' && (
            <FileTree fileTree={fileTree} expandedDirs={expandedDirs} activeTab={activeTab}
              onToggleDir={toggleDir} onOpenFile={openFile} onRefresh={loadRootFiles} onOpenFolder={handleOpenFolder} />
          )}
          {activePanel === 'search' && (
            <SearchPanel query={searchQuery} onQueryChange={setSearchQuery} onSearch={doSearch}
              results={searchResults} onOpenFile={openFile} />
          )}
          {activePanel === 'tools' && (
            <ToolsPanel activeTools={activeTools} />
          )}
          {activePanel === 'git' && (
            <GitPanel />
          )}
        </div>

        <CodeEditor
          tabs={tabs} activeTab={activeTab} onSelectTab={setActiveTab}
          onCloseTab={closeTab} onContentChange={onContentChange} onSave={saveFile}
          modelInfo={modelInfo} onNewConversation={newConversation}
          onOpenChat={() => setChatOpen(true)}
          onApproveDiff={approveDiff}
          onRejectDiff={rejectDiff} />

        {chatOpen && (
          <ChatPanel
            messages={messages} inputValue={inputValue}
            onInputChange={setInputValue} onSend={sendMessage} onStop={stopGeneration}
            onNewConversation={newConversation} onClose={() => setChatOpen(false)}
            isStreaming={isStreaming} connected={connected}
            modelInfo={modelInfo} contentBuffer={contentBufferRef.current}
            onApplyCode={handleApplyCode}
            mode={chatMode} onModeChange={setChatMode} />
        )}
      </div>

      <StatusBar connected={connected} modelInfo={modelInfo} />
    </div>
  );
}
