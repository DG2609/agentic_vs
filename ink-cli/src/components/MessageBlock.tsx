import React from 'react';
import { Box, Text, Newline } from 'ink';
import { theme } from '../theme.js';
import type { Message, ToolCall } from '../types.js';
import ToolBlock from './ToolBlock.js';

interface Props {
  message: Message;
  onToggleTool?: (toolId: string) => void;
}

function renderContent(content: string): React.ReactNode {
  // Simple inline markdown: **bold**, `code`, and plain text
  const parts = content.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <Text key={i} color={theme.codeString} backgroundColor={theme.bgHover}>{part.slice(1, -1)}</Text>;
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <Text key={i} bold color={theme.textBright}>{part.slice(2, -2)}</Text>;
    }
    return <Text key={i} color={theme.text}>{part}</Text>;
  });
}

export default function MessageBlock({ message, onToggleTool }: Props) {
  const isUser = message.role === 'user';
  const time = message.timestamp.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });

  if (isUser) {
    return (
      <Box flexDirection="column" marginBottom={1} paddingX={2}>
        <Box gap={1} justifyContent="flex-end">
          <Text color={theme.textDim}>{time}</Text>
          <Text color={theme.accentBright} bold>you</Text>
        </Box>
        <Box
          borderStyle="round"
          borderColor={theme.accentDim}
          paddingX={1}
          marginLeft={4}
        >
          <Text color={theme.text} wrap="wrap">{message.content}</Text>
        </Box>
      </Box>
    );
  }

  // Assistant message
  return (
    <Box flexDirection="column" marginBottom={1} paddingX={2}>
      <Box gap={1}>
        <Text color={theme.accent} bold>◆ shadowdev</Text>
        <Text color={theme.textDim}>{time}</Text>
        {message.streaming && <Text color={theme.yellow}>●</Text>}
      </Box>

      {/* Tool calls */}
      {message.tools.length > 0 && (
        <Box flexDirection="column" marginTop={0}>
          {message.tools.map(tool => (
            <ToolBlock
              key={tool.tool_id}
              tool={tool}
              onToggle={onToggleTool ? () => onToggleTool(tool.tool_id) : undefined}
            />
          ))}
        </Box>
      )}

      {/* Content */}
      {message.content && (
        <Box paddingLeft={2} marginTop={message.tools.length > 0 ? 1 : 0}>
          <Text color={theme.text} wrap="wrap">
            {renderContent(message.content)}
          </Text>
        </Box>
      )}

      {/* Streaming cursor */}
      {message.streaming && !message.content && (
        <Box paddingLeft={2}>
          <Text color={theme.accent}>▋</Text>
        </Box>
      )}
    </Box>
  );
}
