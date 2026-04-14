import React, { useState } from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { FileNode, WorkerInfo } from '../types.js';

const FILE_ICONS: Record<string, string> = {
  ts: '📘', tsx: '📘', js: '📙', jsx: '📙',
  py: '🐍', md: '📝', json: '📋', css: '🎨',
  html: '🌐', sh: '⚡', yml: '⚙', yaml: '⚙',
};

function getFileIcon(name: string, isDir: boolean): string {
  if (isDir) return '📁';
  const ext = name.split('.').pop() ?? '';
  return FILE_ICONS[ext] ?? '📄';
}

const WORKER_ICONS: Record<string, string> = {
  idle: '○', running: '●', done: '✓', error: '✗',
};
const WORKER_COLORS: Record<string, string> = {
  idle: theme.textDim, running: theme.yellow, done: theme.green, error: theme.red,
};
const ROLE_COLORS: Record<string, string> = {
  explorer: theme.cyan, architect: theme.blue, coder: theme.accent,
  reviewer: theme.orange, qa: theme.yellow, lead: theme.accentBright, general: theme.textMuted,
};

interface FileTreeItemProps {
  node: FileNode;
  depth: number;
}

function FileTreeItem({ node, depth }: FileTreeItemProps) {
  const [open, setOpen] = useState(depth === 0);
  const icon = getFileIcon(node.name, node.type === 'directory');
  const indent = '  '.repeat(depth);

  return (
    <Box flexDirection="column">
      <Box gap={0}>
        <Text color={theme.textDim}>{indent}</Text>
        {node.type === 'directory' && (
          <Text color={theme.textDim}>{open ? '▾ ' : '▸ '}</Text>
        )}
        {node.type === 'file' && <Text color={theme.textDim}>  </Text>}
        <Text color={node.type === 'directory' ? theme.accentBright : theme.textMuted}>
          {icon} {node.name}
        </Text>
      </Box>
      {open && node.children && node.children.map(child => (
        <FileTreeItem key={child.path} node={child} depth={depth + 1} />
      ))}
    </Box>
  );
}

interface Props {
  files: FileNode[];
  workers: WorkerInfo[];
  open: boolean;
}

export default function Sidebar({ files, workers, open }: Props) {
  if (!open) return null;

  return (
    <Box
      flexDirection="column"
      width={28}
      borderStyle="single"
      borderColor={theme.border}
      flexShrink={0}
    >
      {/* File tree section */}
      <Box paddingX={1} paddingY={0} borderStyle="single" borderColor={theme.border}>
        <Text color={theme.accent} bold>EXPLORER</Text>
      </Box>
      <Box flexDirection="column" paddingX={1} flexGrow={1} overflow="hidden">
        {files.length === 0 ? (
          <Text color={theme.textDim}>Loading...</Text>
        ) : (
          files.map(node => (
            <FileTreeItem key={node.path} node={node} depth={0} />
          ))
        )}
      </Box>

      {/* Agent team section — only shown when workers exist */}
      {workers.length > 0 && (
        <>
          <Box paddingX={1} borderStyle="single" borderColor={theme.border}>
            <Text color={theme.accent} bold>AGENT TEAM</Text>
          </Box>
          <Box flexDirection="column" paddingX={1}>
            {workers.map(w => (
              <Box key={w.id} gap={1}>
                <Text color={WORKER_COLORS[w.status] ?? theme.textDim}>
                  {WORKER_ICONS[w.status] ?? '○'}
                </Text>
                <Text color={ROLE_COLORS[w.role] ?? theme.textMuted}>
                  {w.role}
                </Text>
                <Text color={theme.textDim}>{w.id.slice(0, 6)}</Text>
              </Box>
            ))}
          </Box>
        </>
      )}
    </Box>
  );
}
