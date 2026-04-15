import React from 'react';
import { Box, Text, useInput } from 'ink';
import { theme } from '../theme.js';

interface Props {
  provider: string;
  models: string[];
  current: string;
  selectedIdx: number;
  onNavigate: (delta: number) => void;
  onSelect: (model: string) => void;
  onCancel: () => void;
}

export default function ModelPicker({
  provider, models, current, selectedIdx, onNavigate, onSelect, onCancel,
}: Props) {
  useInput((_input, key) => {
    if (key.escape) { onCancel(); return; }
    if (key.upArrow) { onNavigate(-1); return; }
    if (key.downArrow) { onNavigate(1); return; }
    if (key.return && models.length > 0) { onSelect(models[selectedIdx]); return; }
  });

  // Scrolling window of 12
  const winSize = 12;
  const start = Math.max(0, Math.min(selectedIdx - Math.floor(winSize / 2), models.length - winSize));
  const visible = models.slice(start, start + winSize);

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.accent}
      paddingX={1}
    >
      {/* Header */}
      <Box justifyContent="space-between" paddingX={1}>
        <Box gap={1}>
          <Text color={theme.accent} bold>Switch Model</Text>
          <Text color={theme.textDim}>({provider})</Text>
        </Box>
        <Text color={theme.textDim}>↑↓ navigate  Enter select  Esc cancel</Text>
      </Box>

      {models.length === 0 ? (
        <Box paddingX={2} paddingY={1}>
          <Text color={theme.textDim}>No models available for this provider.</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {visible.map((m, vi) => {
            const realIdx = start + vi;
            const isSelected = realIdx === selectedIdx;
            const isCurrent = m === current;

            return (
              <Box key={m} gap={1} paddingX={1}>
                {/* Selection arrow */}
                <Text color={theme.accent}>{isSelected ? '▶' : ' '}</Text>

                {/* Model name */}
                <Text
                  color={isSelected ? theme.accentBright : theme.textMuted}
                  bold={isSelected}
                >
                  {m}
                </Text>

                {/* Current marker */}
                {isCurrent && <Text color={theme.green}> ✓ active</Text>}
              </Box>
            );
          })}
        </Box>
      )}

      {/* Scroll hint */}
      {models.length > winSize && (
        <Box paddingX={2}>
          <Text color={theme.textDim}>
            {selectedIdx + 1}/{models.length} models
          </Text>
        </Box>
      )}
    </Box>
  );
}
