import React from 'react';
import { Box, Text, useInput } from 'ink';
import { theme } from '../theme.js';

export interface ProviderItem {
  id: string;
  label: string;
  type: 'local' | 'cloud';
  model: string;
}

interface Props {
  providers: ProviderItem[];
  current: string;
  selectedIdx: number;
  onNavigate: (delta: number) => void;
  onSelect: (provider: ProviderItem) => void;
  onCancel: () => void;
}

export default function ProviderPicker({
  providers, current, selectedIdx, onNavigate, onSelect, onCancel,
}: Props) {
  useInput((_input, key) => {
    if (key.escape) { onCancel(); return; }
    if (key.upArrow) { onNavigate(-1); return; }
    if (key.downArrow) { onNavigate(1); return; }
    if (key.return) { onSelect(providers[selectedIdx]); return; }
  });

  // Show a window of 12 providers centered on selected
  const winSize = 12;
  const start = Math.max(0, Math.min(selectedIdx - Math.floor(winSize / 2), providers.length - winSize));
  const visible = providers.slice(start, start + winSize);

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.accent}
      paddingX={1}
    >
      {/* Header */}
      <Box justifyContent="space-between" paddingX={1}>
        <Text color={theme.accent} bold>Switch Provider</Text>
        <Text color={theme.textDim}>↑↓ navigate  Enter select  Esc cancel</Text>
      </Box>

      {/* Legend */}
      <Box gap={3} paddingX={1}>
        <Box gap={1}><Text color={theme.green}>●</Text><Text color={theme.textDim}>local</Text></Box>
        <Box gap={1}><Text color={theme.blue}>○</Text><Text color={theme.textDim}>cloud</Text></Box>
      </Box>

      <Box flexDirection="column">
        {visible.map((p, vi) => {
          const realIdx = start + vi;
          const isSelected = realIdx === selectedIdx;
          const isCurrent = p.id === current;
          const isLocal = p.type === 'local';
          const providerDot = isLocal
            ? <Text color={theme.green}>●</Text>
            : <Text color={theme.blue}>○</Text>;

          return (
            <Box
              key={p.id}
              gap={1}
              paddingX={1}
            >
              {/* Selection arrow */}
              <Text color={theme.accent}>{isSelected ? '▶' : ' '}</Text>

              {/* Type dot */}
              {providerDot}

              {/* Label */}
              <Text
                color={isSelected ? theme.textBright : theme.textMuted}
                bold={isSelected}
              >
                {p.label.padEnd(22)}
              </Text>

              {/* Model */}
              <Text color={theme.textDim}>
                {p.model ? p.model.slice(0, 32) : '(not configured)'}
              </Text>

              {/* Current marker */}
              {isCurrent && <Text color={theme.green}> ✓ active</Text>}
            </Box>
          );
        })}
      </Box>

      {/* Footer: scroll hint if list is truncated */}
      {providers.length > winSize && (
        <Box paddingX={2}>
          <Text color={theme.textDim}>
            {selectedIdx + 1}/{providers.length} providers
          </Text>
        </Box>
      )}
    </Box>
  );
}
