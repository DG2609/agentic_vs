import React, { useState, useCallback } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import { theme } from '../theme.js';

const SLASH_COMMANDS = [
  '/help', '/model', '/provider', '/plan', '/config', '/doctor',
  '/fork', '/clear', '/theme', '/exit',
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
  const [hint, setHint] = useState('');

  const handleChange = useCallback((val: string) => {
    setValue(val);
    // Show slash command hint
    if (val.startsWith('/')) {
      const match = SLASH_COMMANDS.find(c => c.startsWith(val) && c !== val);
      setHint(match ? match.slice(val.length) : '');
    } else {
      setHint('');
    }
    setHistoryIdx(-1);
  }, []);

  const handleSubmit = useCallback((val: string) => {
    const trimmed = val.trim();
    if (!trimmed) return;
    if (trimmed === '/exit' || trimmed === '/quit') {
      process.exit(0);
    }
    setHistory(prev => [trimmed, ...prev.slice(0, 49)]);
    setHistoryIdx(-1);
    setValue('');
    setHint('');
    onSubmit(trimmed);
  }, [onSubmit]);

  useInput((input, key) => {
    if (key.upArrow && history.length > 0) {
      const idx = Math.min(historyIdx + 1, history.length - 1);
      setHistoryIdx(idx);
      setValue(history[idx] ?? '');
      setHint('');
    }
    if (key.downArrow) {
      const idx = historyIdx - 1;
      if (idx < 0) {
        setHistoryIdx(-1);
        setValue('');
      } else {
        setHistoryIdx(idx);
        setValue(history[idx] ?? '');
      }
      setHint('');
    }
    // Tab to complete slash command
    if (input === '\t' && hint) {
      setValue(value + hint);
      setHint('');
    }
    // Ctrl+C to stop generation
    if (key.ctrl && input === 'c' && streaming) {
      onCancel();
    }
  });

  const borderColor = streaming ? theme.yellow : disabled ? theme.border : theme.borderActive;
  const prompt = streaming ? '⏸' : '›';
  const promptColor = streaming ? theme.yellow : theme.accent;

  return (
    <Box flexDirection="column">
      {/* Hint bar */}
      {hint && (
        <Box paddingX={2}>
          <Text color={theme.textDim}>Tab to complete: </Text>
          <Text color={theme.accentBright}>{value}</Text>
          <Text color={theme.textDim}>{hint}</Text>
        </Box>
      )}
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
            placeholder={streaming ? 'Generating... (Ctrl+C to stop)' : 'Message ShadowDev...'}
            focus={!disabled}
          />
        </Box>
        {streaming && <Text color={theme.yellow} dimColor>⏳</Text>}
      </Box>
    </Box>
  );
}
