import React, { useState, useCallback, useMemo } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import { theme } from '../theme.js';

interface SlashCommand {
  cmd: string;
  desc: string;
  /** true = executes immediately on Enter (no extra args needed) */
  instant?: boolean;
}

const ALL_COMMANDS: SlashCommand[] = [
  { cmd: '/provider',  desc: 'Switch LLM provider (opens picker)' },
  { cmd: '/model',     desc: 'Show or switch model for current provider' },
  { cmd: '/plan',      desc: 'Enter plan mode' },
  { cmd: '/doctor',    desc: 'Run diagnostics' },
  { cmd: '/config',    desc: 'View or set config settings' },
  { cmd: '/fork',      desc: 'Fork the current session' },
  { cmd: '/help',      desc: 'Show keyboard shortcuts & commands', instant: true },
  { cmd: '/clear',     desc: 'Clear chat history', instant: true },
  { cmd: '/new',       desc: 'Start a new session', instant: true },
  { cmd: '/theme',     desc: 'Cycle color theme', instant: true },
  { cmd: '/exit',      desc: 'Exit ShadowDev', instant: true },
  { cmd: '/intel',     desc: 'Start PromptIntel continuous improvement engine', instant: true },
  { cmd: '/quality',   desc: 'Start QualityIntel review→improve→converge loop', instant: true },
  { cmd: '/plugins',            desc: 'Open plugin manager (install/uninstall/search hub)', instant: true },
  { cmd: '/plugin install',     desc: 'Open install wizard for a hub plugin: /plugin install <name>' },
  { cmd: '/plugin audit',       desc: 'Audit a hub plugin (no install): /plugin audit <name>' },
  { cmd: '/plugin uninstall',   desc: 'Uninstall a plugin: /plugin uninstall <name>' },
];

interface Props {
  onSubmit: (value: string) => void;
  onCancel: () => void;
  disabled?: boolean;
  streaming?: boolean;
}

export default function InputBox({ onSubmit, onCancel, disabled, streaming }: Props) {
  const [value, setValue] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [paletteIdx, setPaletteIdx] = useState(0);

  // Show palette whenever value starts with /
  const showPalette = value.startsWith('/') && !streaming && !disabled;

  const filtered: SlashCommand[] = useMemo(() => {
    if (!showPalette) return [];
    const q = value.trim().toLowerCase();
    if (q === '/') return ALL_COMMANDS;                 // bare / → show all
    return ALL_COMMANDS.filter(c =>
      c.cmd.startsWith(q) ||                           // /mod → /model
      q.startsWith(c.cmd),                             // /model<space> still matches /model
    );
  }, [value, showPalette]);

  // Reset palette index when filtered list changes
  const clampedIdx = Math.min(paletteIdx, Math.max(0, filtered.length - 1));

  const handleChange = useCallback((val: string) => {
    setValue(val);
    setPaletteIdx(0);
    setHistoryIdx(-1);
  }, []);

  const handleSubmit = useCallback((val: string) => {
    const trimmed = val.trim();
    if (!trimmed) return;

    // If palette is open and user hits Enter with a selection → use that command
    if (trimmed.startsWith('/') && filtered.length > 0) {
      const selected = filtered[clampedIdx];
      if (selected && trimmed === '/') {
        // bare "/" → fill in selected command
        const filled = selected.instant ? selected.cmd : selected.cmd + ' ';
        setValue(selected.instant ? '' : filled);
        if (selected.instant) {
          setHistory(prev => [selected.cmd, ...prev.slice(0, 49)]);
          onSubmit(selected.cmd);
        } else {
          setValue(filled);
        }
        setPaletteIdx(0);
        return;
      }
    }

    if (trimmed === '/exit' || trimmed === '/quit') process.exit(0);

    setHistory(prev => [trimmed, ...prev.slice(0, 49)]);
    setHistoryIdx(-1);
    setValue('');
    setPaletteIdx(0);
    onSubmit(trimmed);
  }, [onSubmit, filtered, clampedIdx]);

  useInput((input, key) => {
    // Inside palette: ↑↓ navigate, Enter selects, Esc closes
    if (showPalette && filtered.length > 0) {
      if (key.upArrow) {
        setPaletteIdx(i => Math.max(0, i - 1));
        return;
      }
      if (key.downArrow) {
        setPaletteIdx(i => Math.min(filtered.length - 1, i + 1));
        return;
      }
      if (key.escape) {
        setValue('');
        setPaletteIdx(0);
        return;
      }
      if (key.tab) {
        // Tab fills in the highlighted command
        const sel = filtered[clampedIdx];
        if (sel) {
          const filled = sel.instant ? sel.cmd : sel.cmd + ' ';
          setValue(filled);
          setPaletteIdx(0);
        }
        return;
      }
    }

    // Outside palette: history navigation
    if (!showPalette) {
      if (key.upArrow && history.length > 0) {
        const idx = Math.min(historyIdx + 1, history.length - 1);
        setHistoryIdx(idx);
        setValue(history[idx] ?? '');
        return;
      }
      if (key.downArrow) {
        const idx = historyIdx - 1;
        setHistoryIdx(idx);
        setValue(idx < 0 ? '' : (history[idx] ?? ''));
        return;
      }
    }

    // Ctrl+C stops generation
    if (key.ctrl && input === 'c' && streaming) {
      onCancel();
    }
  });

  const borderColor = streaming
    ? theme.yellow
    : disabled
    ? theme.border
    : showPalette
    ? theme.accent
    : theme.borderActive;

  const prompt = streaming ? '⏸' : '›';
  const promptColor = streaming ? theme.yellow : theme.accent;

  return (
    <Box flexDirection="column">
      {/* Command Palette — appears ABOVE input when typing / */}
      {showPalette && filtered.length > 0 && (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={theme.accent}
          marginX={1}
          paddingX={1}
        >
          {/* Palette header */}
          <Box justifyContent="space-between">
            <Text color={theme.accent} bold>Commands</Text>
            <Text color={theme.textDim}>↑↓ navigate  Tab fill  Enter run  Esc cancel</Text>
          </Box>

          {/* Command rows */}
          {filtered.map((c, i) => {
            const isSelected = i === clampedIdx;
            return (
              <Box key={c.cmd} gap={1}>
                <Text color={isSelected ? theme.accent : theme.textDim}>
                  {isSelected ? '▶' : ' '}
                </Text>
                <Text
                  color={isSelected ? theme.accentBright : theme.textMuted}
                  bold={isSelected}
                >
                  {c.cmd.padEnd(14)}
                </Text>
                <Text color={theme.textDim}>{c.desc}</Text>
                {c.instant && (
                  <Text color={theme.green}> ↵</Text>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      {/* No match hint */}
      {showPalette && filtered.length === 0 && (
        <Box paddingX={2}>
          <Text color={theme.red}>No matching command — </Text>
          <Text color={theme.textDim}>Esc to cancel</Text>
        </Box>
      )}

      {/* Input row */}
      <Box
        borderStyle="round"
        borderColor={borderColor}
        paddingX={1}
        gap={1}
      >
        <Text color={promptColor} bold>{prompt}</Text>
        <Box flexGrow={1}>
          <TextInput
            value={value}
            onChange={handleChange}
            onSubmit={handleSubmit}
            placeholder={
              streaming
                ? 'Generating...  Ctrl+C to stop'
                : 'Message ShadowDev...  or / for commands'
            }
            focus={!disabled}
          />
        </Box>
        {streaming && <Text color={theme.yellow} dimColor>⏳</Text>}
      </Box>
    </Box>
  );
}
