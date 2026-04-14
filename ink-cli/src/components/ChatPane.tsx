import React from 'react';
import { Box, Text, Static } from 'ink';
import { theme } from '../theme.js';
import type { Message } from '../types.js';
import MessageBlock from './MessageBlock.js';

interface Props {
  messages: Message[];
  onToggleTool: (msgId: string, toolId: string) => void;
  sidebarOpen: boolean;
}

export default function ChatPane({ messages, onToggleTool, sidebarOpen }: Props) {
  // Static renders messages that don't change; last message (streaming) renders below
  const settled = messages.slice(0, -1);
  const last = messages[messages.length - 1];

  if (messages.length === 0) {
    return (
      <Box flexGrow={1} flexDirection="column" alignItems="center" justifyContent="center">
        <Text color={theme.accent} bold>◆ ShadowDev</Text>
        <Text color={theme.textMuted}>AI coding assistant · {91} tools available</Text>
        <Text color={theme.textDim}>Type a message or /help for commands</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexGrow={1} overflow="hidden">
      <Static items={settled}>
        {(msg) => (
          <MessageBlock
            key={msg.id}
            message={msg}
            onToggleTool={(toolId) => onToggleTool(msg.id, toolId)}
          />
        )}
      </Static>
      {last && (
        <MessageBlock
          message={last}
          onToggleTool={(toolId) => onToggleTool(last.id, toolId)}
        />
      )}
    </Box>
  );
}
