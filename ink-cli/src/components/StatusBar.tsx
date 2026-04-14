import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { AppConfig } from '../types.js';

const LOCAL_PROVIDERS = new Set(['ollama', 'vllm', 'llamacpp', 'lmstudio', 'openai_compatible']);

interface Props {
  connected: boolean;
  config: AppConfig;
  threadId: string;
  tokenCount?: number;
  sidebarOpen: boolean;
  streaming: boolean;
}

export default function StatusBar({ connected, config, threadId, tokenCount, sidebarOpen, streaming }: Props) {
  const connColor = connected ? theme.green : theme.red;
  const connIcon = connected ? '●' : '○';
  const connLabel = connected ? 'connected' : 'offline';

  const isLocal = LOCAL_PROVIDERS.has(config.provider);
  const providerColor = isLocal ? theme.green : theme.blue;
  const providerLabel = isLocal ? `● LOCAL · ${config.provider}` : `● ${config.provider}`;

  return (
    <Box
      borderStyle="single"
      borderColor={theme.border}
      paddingX={1}
      justifyContent="space-between"
    >
      {/* Left: connection + provider + model */}
      <Box gap={2}>
        <Text color={connColor}>{connIcon} {connLabel}</Text>
        <Text color={theme.border}>│</Text>
        <Text color={providerColor}>{providerLabel}</Text>
        <Text color={theme.textDim}>·</Text>
        <Text color={theme.textMuted}>{config.model || '...'}</Text>
      </Box>

      {/* Center: streaming indicator */}
      {streaming && (
        <Box>
          <Text color={theme.yellow}>⚡ generating</Text>
        </Box>
      )}

      {/* Right: tokens + session + sidebar hint */}
      <Box gap={2}>
        {tokenCount !== undefined && tokenCount > 0 && (
          <Text color={theme.textDim}>tok: {tokenCount.toLocaleString()}</Text>
        )}
        <Text color={theme.textDim}>#{threadId.slice(0, 8)}</Text>
        <Text color={theme.textDim}>[Ctrl+B] {sidebarOpen ? 'hide' : 'show'} sidebar</Text>
      </Box>
    </Box>
  );
}
