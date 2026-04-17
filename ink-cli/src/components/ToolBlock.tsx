import React from 'react';
import { Box, Text } from 'ink';
import Spinner from 'ink-spinner';
import { theme } from '../theme.js';
import type { ToolCall } from '../types.js';

const TOOL_ICONS: Record<string, string> = {
  file_read: '📄', file_write: '✏️', file_edit: '✏️', file_list: '📁',
  glob_search: '🔍', code_search: '🔎', grep_search: '🔎', web_search: '🌐',
  webfetch: '🌐', terminal_exec: '⚡', git_status: '🌿', git_diff: '🌿',
  git_commit: '🌿', git_log: '🌿', memory_save: '💾', memory_search: '💾',
  todo_read: '📋', todo_write: '📋', plan_enter: '📐', plan_exit: '📐',
  run_tests: '🧪', code_analyze: '🔬', diagnostics: '🩺',
};

interface Props {
  tool: ToolCall;
  onToggle?: () => void;
}

export default function ToolBlock({ tool, onToggle }: Props) {
  const icon = TOOL_ICONS[tool.tool_name] ?? '⚙';
  const isRunning = tool.status === 'running';
  const isCompleted = tool.status === 'completed';

  const statusColor = isRunning ? theme.yellow : isCompleted ? theme.green : theme.red;
  const statusIcon = isRunning ? null : isCompleted ? '✓' : '✗';
  const elapsed = tool.elapsed_ms ? ` ${(tool.elapsed_ms / 1000).toFixed(1)}s` : '';

  return (
    <Box flexDirection="column" marginLeft={2} marginBottom={tool.expanded ? 1 : 0}>
      {/* Header row */}
      <Box gap={1}>
        <Text color={theme.textDim}>│</Text>
        {isRunning && (
          <Text color={theme.yellow}>
            <Spinner type="dots" />
          </Text>
        )}
        {!isRunning && <Text color={statusColor}>{statusIcon}</Text>}
        <Text color={theme.textMuted}>{icon}</Text>
        <Text color={theme.accent} bold>{tool.tool_name}</Text>
        {!isRunning && (
          <Text color={theme.textDim}>{elapsed}</Text>
        )}
        {onToggle && !isRunning && (
          <Text color={theme.textDim}> [{tool.expanded ? '−' : '+'}]</Text>
        )}
      </Box>

      {/* Expanded output */}
      {tool.expanded && tool.output && (
        <Box flexDirection="column" marginLeft={4} marginTop={0}>
          <Box borderStyle="single" borderColor={theme.border} paddingX={1}>
            <Text color={theme.textMuted} wrap="wrap">
              {tool.output.slice(0, 800)}
              {tool.output.length > 800
                ? `\n… (+${tool.output.length - 800} chars truncated — open full output with the server log)`
                : ''}
            </Text>
          </Box>
        </Box>
      )}
    </Box>
  );
}
