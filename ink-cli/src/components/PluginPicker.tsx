import React, { useState, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import Spinner from 'ink-spinner';
import { theme } from '../theme.js';
import type {
  PluginsState,
  HubPlugin,
  InstalledPlugin,
} from '../hooks/usePlugins.js';

interface Props {
  plugins: PluginsState;
  onClose: () => void;
  onPickInstall: (plugin: HubPlugin) => void;
}

type Side = 'installed' | 'hub';

export function PluginPicker({ plugins, onClose, onPickInstall }: Props) {
  const [side, setSide] = useState<Side>('installed');
  const [leftIdx, setLeftIdx] = useState(0);
  const [rightIdx, setRightIdx] = useState(0);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [editingQuery, setEditingQuery] = useState(false);

  useEffect(() => {
    if (leftIdx >= plugins.installed.length) {
      setLeftIdx(Math.max(0, plugins.installed.length - 1));
    }
  }, [plugins.installed.length, leftIdx]);

  useEffect(() => {
    if (rightIdx >= plugins.hubResults.length) {
      setRightIdx(Math.max(0, plugins.hubResults.length - 1));
    }
  }, [plugins.hubResults.length, rightIdx]);

  const runSearch = async (q: string) => {
    setSearching(true);
    try {
      await plugins.searchHub(q);
    } finally {
      setSearching(false);
    }
  };

  useInput((input, key) => {
    if (editingQuery) {
      if (key.return) {
        setEditingQuery(false);
        runSearch(query);
      } else if (key.escape) {
        setEditingQuery(false);
      }
      return;
    }

    if (key.escape) {
      onClose();
      return;
    }
    if (key.tab) {
      setSide(s => (s === 'installed' ? 'hub' : 'installed'));
      return;
    }
    if (input === '/') {
      setSide('hub');
      setEditingQuery(true);
      return;
    }
    if (key.upArrow) {
      if (side === 'installed') setLeftIdx(i => Math.max(0, i - 1));
      else setRightIdx(i => Math.max(0, i - 1));
      return;
    }
    if (key.downArrow) {
      if (side === 'installed') {
        setLeftIdx(i => Math.min(Math.max(0, plugins.installed.length - 1), i + 1));
      } else {
        setRightIdx(i => Math.min(Math.max(0, plugins.hubResults.length - 1), i + 1));
      }
      return;
    }
    if (key.return && side === 'hub') {
      const sel = plugins.hubResults[rightIdx];
      if (sel) onPickInstall(sel);
      return;
    }
    if (input === 'd' && side === 'installed') {
      const sel = plugins.installed[leftIdx];
      if (sel) plugins.uninstall(sel.name).catch(() => {});
      return;
    }
    if (input === 'r') {
      plugins.refreshInstalled();
      return;
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.accent} padding={1}>
      <Text bold color={theme.accentBright}>
        Plugins  ·  Tab switch  ·  / search  ·  Enter install  ·  d uninstall  ·  Esc close
      </Text>
      <Box marginTop={1} flexDirection="row">
        {/* LEFT: installed */}
        <Box
          flexDirection="column"
          width="50%"
          borderStyle="round"
          borderColor={side === 'installed' ? theme.accentBright : theme.border}
          padding={1}
        >
          <Text bold>Installed ({plugins.installed.length})</Text>
          {plugins.installed.length === 0 && (
            <Text color={theme.textDim}>No plugins installed yet.</Text>
          )}
          {plugins.installed.map((p: InstalledPlugin, i: number) => (
            <Box key={p.name} flexDirection="row">
              <Text
                color={
                  i === leftIdx && side === 'installed'
                    ? theme.accentBright
                    : p.status === 'error'
                    ? theme.red
                    : theme.text
                }
              >
                {i === leftIdx && side === 'installed' ? '> ' : '  '}
                {p.name}@{p.version}  [{p.status}]  score={p.score}
              </Text>
            </Box>
          ))}
        </Box>

        {/* RIGHT: hub search */}
        <Box
          flexDirection="column"
          width="50%"
          borderStyle="round"
          borderColor={side === 'hub' ? theme.accentBright : theme.border}
          padding={1}
        >
          <Box flexDirection="row">
            <Text bold>Hub  </Text>
            {editingQuery ? (
              <TextInput value={query} onChange={setQuery} onSubmit={() => { setEditingQuery(false); runSearch(query); }} />
            ) : (
              <Text color={theme.textDim}>
                {query ? `q: ${query}` : 'press / to search'}
              </Text>
            )}
          </Box>
          {searching && (
            <Text color={theme.accent}>
              <Spinner type="dots" /> searching…
            </Text>
          )}
          {!searching && plugins.hubResults.length === 0 && (
            <Text color={theme.textDim}>No results. Press / to search.</Text>
          )}
          {plugins.hubResults.map((p: HubPlugin, i: number) => (
            <Box key={p.name} flexDirection="column">
              <Text color={i === rightIdx && side === 'hub' ? theme.accentBright : theme.text}>
                {i === rightIdx && side === 'hub' ? '> ' : '  '}
                {p.name}@{p.version}  ({p.tool_count} tools)
              </Text>
              {i === rightIdx && side === 'hub' && (
                <Text color={theme.textDim}>    {p.description}</Text>
              )}
            </Box>
          ))}
        </Box>
      </Box>
      {plugins.lastError && (
        <Box marginTop={1}>
          <Text color={theme.red}>⚠ {plugins.lastError}</Text>
        </Box>
      )}
    </Box>
  );
}
