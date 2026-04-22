import React, { useState, useCallback, useEffect } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import Spinner from 'ink-spinner';
import { useServer } from './hooks/useServer.js';
import { useSocket } from './hooks/useSocket.js';
import { useIntel } from './hooks/useIntel.js';
import { useQuality } from './hooks/useQuality.js';
import { usePlugins } from './hooks/usePlugins.js';
import type { HubPlugin } from './hooks/usePlugins.js';
import ChatPane from './components/ChatPane.js';
import Sidebar from './components/Sidebar.js';
import InputBox from './components/InputBox.js';
import StatusBar from './components/StatusBar.js';
import ProviderPicker from './components/ProviderPicker.js';
import ModelPicker from './components/ModelPicker.js';
import IntelPanel from './components/IntelPanel.js';
import QualityPanel from './components/QualityPanel.js';
import { PluginPicker } from './components/PluginPicker.js';
import { InstallWizard } from './components/InstallWizard.js';
import { QualityReport } from './components/QualityReport.js';
import { theme } from './theme.js';
import type { FileNode } from './types.js';
import type { ProviderItem } from './components/ProviderPicker.js';

const BACKEND = 'http://localhost:8000';

const THEMES = ['dark', 'monokai', 'nord'] as const;
type ThemeName = typeof THEMES[number];

const THEME_ACCENT: Record<ThemeName, string> = {
  dark:    '#8b5cf6',
  monokai: '#a6e22e',
  nord:    '#88c0d0',
};

const HELP_TEXT = `◆ ShadowDev — Commands & Shortcuts

  /provider         Open provider picker (19 providers)
  /provider <name>  Switch provider directly
  /model            Show current model
  /model <name>     Switch model for current provider
  /theme            Cycle color theme (dark → monokai → nord)
  /doctor           Show backend health & diagnostics
  /config           Show current config (provider, model, workspace)
  /clear            Clear chat history
  /new              Start a new session
  /fork             Fork current session
  /plan             Enter plan mode
  /plugins          Open plugin manager
  /plugin install <name>    Open install wizard
  /plugin audit <name>      Audit a hub plugin
  /plugin uninstall <name>  Uninstall a plugin
  /help             Show this help
  /exit             Exit ShadowDev

  Ctrl+B   Toggle sidebar (file tree + agent team)
  Ctrl+L   Clear chat
  Ctrl+N   New session
  Ctrl+C   Stop generation / Exit`;

export default function App() {
  const { exit } = useApp();
  const server = useServer();
  const socket = useSocket(server.isReady);
  const intel = useIntel(socket.socket);
  const quality = useQuality(socket.socket);
  const plugins = usePlugins(socket.socket);

  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [wizardPlugin, setWizardPlugin] = useState<HubPlugin | null>(null);

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const [files, setFiles] = useState<FileNode[]>([]);
  const [tokenCount, setTokenCount] = useState(0);
  const [themeName, setThemeName] = useState<ThemeName>('dark');
  const accentColor = THEME_ACCENT[themeName];

  // Provider picker state
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerProviders, setPickerProviders] = useState<ProviderItem[]>([]);
  const [pickerIdx, setPickerIdx] = useState(0);

  // Model picker state
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [modelList, setModelList] = useState<string[]>([]);
  const [modelPickerIdx, setModelPickerIdx] = useState(0);

  // Fetch files when sidebar opens
  useEffect(() => {
    if (!sidebarOpen || !server.isReady) return;
    fetch(`${BACKEND}/api/files`)
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

  // Open provider picker
  const openProviderPicker = useCallback(() => {
    fetch(`${BACKEND}/api/providers`)
      .then(r => r.json())
      .then((data: { current: string; providers: ProviderItem[] }) => {
        setPickerProviders(data.providers);
        const idx = data.providers.findIndex(p => p.id === data.current);
        setPickerIdx(idx >= 0 ? idx : 0);
        setPickerOpen(true);
      })
      .catch(() => {
        // fallback: inject error msg
        socket.sendMessage('Could not load providers — is the backend running?', threadId);
      });
  }, [socket, threadId]);

  // Open model picker for current provider
  const openModelPicker = useCallback(() => {
    fetch(`${BACKEND}/api/models?provider=${socket.config.provider}`)
      .then(r => r.json())
      .then((data: { models: string[]; current: string }) => {
        setModelList(data.models);
        const idx = data.models.indexOf(data.current);
        setModelPickerIdx(idx >= 0 ? idx : 0);
        setModelPickerOpen(true);
      })
      .catch(() => {
        socket.injectMessage(`Could not load models for ${socket.config.provider}`);
      });
  }, [socket]);

  const handleModelSelect = useCallback((model: string) => {
    setModelPickerOpen(false);
    fetch(`${BACKEND}/api/provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: socket.config.provider, model }),
    })
      .then(r => r.json())
      .then((d: { error?: string; model?: string }) => {
        if (d.error) {
          socket.injectMessage(`Error: ${d.error}`);
        } else {
          socket.updateConfig({ model: d.model ?? model });
          socket.injectMessage(`Model switched to: ${d.model ?? model}`);
        }
      })
      .catch(err => socket.injectMessage(`Failed to switch model: ${String(err)}`));
  }, [socket]);

  const handlePickerSelect = useCallback((provider: ProviderItem) => {
    setPickerOpen(false);
    fetch(`${BACKEND}/api/provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: provider.id }),
    })
      .then(r => r.json())
      .then((data: { status: string; provider: string; label: string; model: string }) => {
        // Update socket config display immediately
        socket.updateConfig?.({ provider: data.provider, model: data.model });
      })
      .catch(() => {});
  }, [socket]);

  // Keyboard shortcuts
  useInput((input, key) => {
    if (pickerOpen || modelPickerOpen || pluginsOpen || wizardPlugin) return; // overlays handle their own input
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

    // ── All slash commands handled 100% locally — never sent to AI ──

    // /clear
    if (trimmed === '/clear') {
      socket.clearMessages();
      return;
    }

    // /exit /quit
    if (trimmed === '/exit' || trimmed === '/quit') {
      process.exit(0);
    }

    // /new /reset
    if (trimmed === '/new' || trimmed === '/reset') {
      setThreadId(crypto.randomUUID());
      socket.clearMessages();
      socket.injectMessage('New session started.');
      return;
    }

    // /help
    if (trimmed === '/help') {
      socket.injectMessage(HELP_TEXT);
      return;
    }

    // /theme — cycle through themes locally, no AI call
    if (trimmed === '/theme') {
      setThemeName(prev => {
        const idx = THEMES.indexOf(prev);
        const next = THEMES[(idx + 1) % THEMES.length];
        socket.injectMessage(`Theme switched to: ${next}`);
        return next;
      });
      return;
    }

    // /provider → picker; /provider <name> → direct switch
    if (trimmed === '/provider') {
      openProviderPicker();
      return;
    }
    if (trimmed.startsWith('/provider ')) {
      const pid = trimmed.slice('/provider '.length).trim();
      fetch(`${BACKEND}/api/provider`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: pid }),
      })
        .then(r => r.json())
        .then((d: { error?: string; provider?: string; label?: string; model?: string }) => {
          if (d.error) {
            socket.injectMessage(`Error: ${d.error}`);
          } else {
            socket.updateConfig({ provider: d.provider ?? pid, model: d.model ?? '' });
            socket.injectMessage(`Provider switched to ${d.label ?? d.provider} (${d.model})`);
          }
        })
        .catch(err => socket.injectMessage(`Failed to switch provider: ${String(err)}`));
      return;
    }

    // /model — open model picker; /model <name> — switch model directly
    if (trimmed === '/model') {
      openModelPicker();
      return;
    }
    if (trimmed.startsWith('/model ')) {
      const modelName = trimmed.slice('/model '.length).trim();
      fetch(`${BACKEND}/api/provider`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: socket.config.provider, model: modelName }),
      })
        .then(r => r.json())
        .then((d: { error?: string; provider?: string; model?: string }) => {
          if (d.error) {
            socket.injectMessage(`Error: ${d.error}`);
          } else {
            socket.updateConfig({ model: d.model ?? modelName });
            socket.injectMessage(`Model switched to: ${d.model ?? modelName}`);
          }
        })
        .catch(err => socket.injectMessage(`Failed to switch model: ${String(err)}`));
      return;
    }

    // /doctor — fetch /health and show diagnostics
    if (trimmed === '/doctor') {
      fetch(`${BACKEND}/health`)
        .then(r => r.json())
        .then((d: { status?: string; tools?: number; model?: string }) => {
          socket.injectMessage(
            `Backend health: ${d.status ?? 'unknown'}\n` +
            `Tools loaded: ${d.tools ?? '?'}\n` +
            `Active model: ${d.model ?? socket.config.model}\n` +
            `Provider: ${socket.config.provider}`
          );
        })
        .catch(() => socket.injectMessage('Could not reach backend — is it running?'));
      return;
    }

    // /config — show current runtime config
    if (trimmed === '/config') {
      fetch(`${BACKEND}/api/model`)
        .then(r => r.json())
        .then((d: { provider?: string; model?: string; is_local?: boolean }) => {
          socket.injectMessage(
            `Config snapshot:\n` +
            `  provider  ${d.provider ?? socket.config.provider}\n` +
            `  model     ${d.model ?? socket.config.model}\n` +
            `  type      ${d.is_local ? 'LOCAL' : 'cloud'}\n` +
            `  theme     ${themeName}`
          );
        })
        .catch(() => socket.injectMessage('Could not fetch config.'));
      return;
    }

    // /fork — duplicate session into new threadId
    if (trimmed === '/fork') {
      setThreadId(crypto.randomUUID());
      socket.injectMessage('Session forked — continuing with a new thread ID.');
      return;
    }

    // /intel — start PromptIntel continuous improvement engine
    if (trimmed === '/intel') {
      socket.injectMessage('PromptIntel started — mining CC source...');
      fetch(`${BACKEND}/api/intel/start`, { method: 'POST' }).catch(() => {});
      return;
    }

    // /intel stop — stop PromptIntel
    if (trimmed === '/intel stop') {
      socket.injectMessage('PromptIntel stopping...');
      fetch(`${BACKEND}/api/intel/stop`, { method: 'POST' }).catch(() => {});
      return;
    }

    // /quality — start QualityIntel review→improve loop
    if (trimmed === '/quality') {
      socket.injectMessage('QualityIntel started — scanning project quality...');
      fetch(`${BACKEND}/api/quality/start`, { method: 'POST' }).catch(() => {});
      return;
    }

    // /quality stop — stop QualityIntel
    if (trimmed === '/quality stop') {
      socket.injectMessage('QualityIntel stopping...');
      fetch(`${BACKEND}/api/quality/stop`, { method: 'POST' }).catch(() => {});
      return;
    }

    // /plugins — open plugin manager overlay
    if (trimmed === '/plugins') {
      setPluginsOpen(true);
      return;
    }

    // /plugin install <name>
    if (trimmed.startsWith('/plugin install ')) {
      const name = trimmed.slice('/plugin install '.length).trim();
      if (!name) {
        socket.injectMessage('Usage: /plugin install <name>');
        return;
      }
      fetch(`${BACKEND}/api/plugins/inspect?name=${encodeURIComponent(name)}`)
        .then(r => r.json())
        .then((d: HubPlugin | { error?: string }) => {
          if ('error' in d && d.error) {
            socket.injectMessage(`Error: ${d.error}`);
          } else {
            setWizardPlugin(d as HubPlugin);
          }
        })
        .catch(err => socket.injectMessage(`Failed to inspect plugin: ${String(err)}`));
      return;
    }

    // /plugin audit <name>
    if (trimmed.startsWith('/plugin audit ')) {
      const name = trimmed.slice('/plugin audit '.length).trim();
      if (!name) {
        socket.injectMessage('Usage: /plugin audit <name>');
        return;
      }
      socket.injectMessage(`Auditing ${name}…`);
      plugins
        .runAudit(name)
        .then(r => {
          const blocks = r.blockers.length;
          const issues = r.issues.length;
          socket.injectMessage(
            `Audit ${name}: score ${r.score}/100 ${r.blocked ? '[BLOCKED] ' : ''}` +
            `(${blocks} blockers, ${issues} warnings)`,
          );
        })
        .catch(err => socket.injectMessage(`Audit failed: ${String(err)}`));
      return;
    }

    // /plugin uninstall <name>
    if (trimmed.startsWith('/plugin uninstall ')) {
      const name = trimmed.slice('/plugin uninstall '.length).trim();
      if (!name) {
        socket.injectMessage('Usage: /plugin uninstall <name>');
        return;
      }
      plugins
        .uninstall(name)
        .then(() => socket.injectMessage(`Uninstalled ${name}.`))
        .catch(err => socket.injectMessage(`Uninstall failed: ${String(err)}`));
      return;
    }

    // /plan — send to AI (it's a mode toggle, not a UI-only command)
    // Everything else → send to AI
    socket.sendMessage(trimmed, threadId);
  }, [socket, threadId, themeName, openProviderPicker, openModelPicker, plugins]);

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

      {/* Provider picker overlay */}
      {pickerOpen && (
        <ProviderPicker
          providers={pickerProviders}
          current={socket.config.provider}
          selectedIdx={pickerIdx}
          onNavigate={(delta) =>
            setPickerIdx(i => Math.max(0, Math.min(pickerProviders.length - 1, i + delta)))
          }
          onSelect={handlePickerSelect}
          onCancel={() => setPickerOpen(false)}
        />
      )}

      {/* Model picker overlay */}
      {modelPickerOpen && (
        <ModelPicker
          provider={socket.config.provider}
          models={modelList}
          current={socket.config.model}
          selectedIdx={modelPickerIdx}
          onNavigate={(delta) =>
            setModelPickerIdx(i => Math.max(0, Math.min(modelList.length - 1, i + delta)))
          }
          onSelect={handleModelSelect}
          onCancel={() => setModelPickerOpen(false)}
        />
      )}

      {/* Plugin picker overlay */}
      {pluginsOpen && !wizardPlugin && (
        <PluginPicker
          plugins={plugins}
          onClose={() => setPluginsOpen(false)}
          onPickInstall={(p) => { setWizardPlugin(p); }}
        />
      )}

      {/* Install wizard overlay */}
      {wizardPlugin && (
        <InstallWizard
          plugin={wizardPlugin}
          plugins={plugins}
          onClose={() => { setWizardPlugin(null); }}
        />
      )}

      {/* Main content row */}
      <Box flexGrow={1} overflow="hidden" flexDirection="column">
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
        {(intel.running || intel.round > 0) && <IntelPanel intel={intel} />}
        {(quality.running || quality.round > 0 || quality.converged) && <QualityPanel quality={quality} />}
      </Box>

      {/* Input */}
      <InputBox
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        disabled={pickerOpen || modelPickerOpen || pluginsOpen || wizardPlugin !== null}
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
