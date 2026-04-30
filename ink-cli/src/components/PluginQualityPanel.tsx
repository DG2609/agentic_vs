import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { PluginQualityState, PQFeedItem } from '../hooks/usePluginQuality.js';

interface Props {
  pq: PluginQualityState;
}

const FEED_VISIBLE = 8;
const BAR_WIDTH = 10;

function scoreColor(score: number): string {
  if (score < 40) return theme.red;
  if (score < 70) return theme.yellow;
  return theme.green;
}

function progressBar(score: number): string {
  const filled = Math.round(score / BAR_WIDTH);
  return '█'.repeat(filled) + '░'.repeat(BAR_WIDTH - filled);
}

function feedIcon(item: PQFeedItem): string {
  switch (item.type) {
    case 'scanning':    return '[s]';
    case 'finding':     return '[f]';
    case 'issue':       return '[!]';
    case 'round_start': return '[>]';
    case 'round_done':  return '[=]';
    case 'scores':      return '[#]';
    case 'converged':   return '[*]';
    default:            return '[ ]';
  }
}

function feedIconColor(item: PQFeedItem): string {
  switch (item.type) {
    case 'scanning':    return theme.textDim;
    case 'finding':     return theme.cyan;
    case 'issue':
      if (item.severity === 'high')   return theme.red;
      if (item.severity === 'medium') return theme.yellow;
      return theme.textMuted;
    case 'round_start': return theme.blue;
    case 'round_done':  return theme.blue;
    case 'scores':      return theme.accentBright;
    case 'converged':   return theme.green;
    default:            return theme.textDim;
  }
}

export default function PluginQualityPanel({ pq }: Props) {
  const { running, converged, round, overallScore, totalIssues, scores, feed } = pq;

  const statusText = converged ? 'Converged' : running ? 'Running' : 'Stopped';
  const statusColor = converged ? theme.green : running ? theme.green : theme.textMuted;
  const roundText = round > 0 ? `Round ${round}` : 'Idle';

  const domainEntries = Object.entries(scores);
  const visibleFeed = feed.slice(-FEED_VISIBLE);

  if (!running && round === 0 && !converged) {
    return (
      <Box
        borderStyle="round"
        borderColor={theme.border}
        flexDirection="column"
        marginX={1}
        paddingX={1}
        paddingY={0}
      >
        <Box gap={1}>
          <Text color={theme.accent} bold>◆ PluginQuality</Text>
          <Text color={theme.textDim}>—</Text>
          <Text color={theme.textMuted}>Press /plugin-quality to start</Text>
        </Box>
      </Box>
    );
  }

  return (
    <Box
      borderStyle="round"
      borderColor={converged ? theme.green : running ? theme.accent : theme.border}
      flexDirection="column"
      marginX={1}
    >
      {/* Header */}
      <Box paddingX={1} gap={2}>
        <Text color={theme.accent} bold>◆ PluginQuality</Text>
        <Text color={theme.textDim}>—</Text>
        <Text color={theme.textMuted}>{roundText}</Text>
        <Text color={theme.textDim}>—</Text>
        <Text color={statusColor}>{statusText}</Text>
        {converged && (
          <>
            <Text color={theme.textDim}>—</Text>
            <Text color={theme.green} bold>plugin subsystem clean</Text>
          </>
        )}
      </Box>

      {/* Stats */}
      <Box
        paddingX={1}
        gap={2}
        borderStyle="single"
        borderColor={theme.border}
        borderTop={false}
        borderLeft={false}
        borderRight={false}
      >
        <Text color={theme.textBright}>
          Overall: <Text color={scoreColor(overallScore)} bold>{overallScore}/100</Text>
        </Text>
        <Text color={theme.textDim}>|</Text>
        <Text color={theme.textMuted}>
          {totalIssues} issue{totalIssues !== 1 ? 's' : ''} found
        </Text>
        <Text color={theme.textDim}>|</Text>
        <Text color={theme.textMuted}>Round {round}</Text>
      </Box>

      {/* Domain scores */}
      {domainEntries.length > 0 && (
        <Box flexDirection="column" paddingX={1} paddingY={0}>
          <Text color={theme.textDim} bold>DOMAIN SCORES</Text>
          {domainEntries.map(([domain, score]) => (
            <Box key={domain} gap={1}>
              <Text color={theme.textMuted}>{domain.padEnd(20)}</Text>
              <Text color={scoreColor(score)}>{progressBar(score)}</Text>
              <Text color={scoreColor(score)} bold>{score}</Text>
            </Box>
          ))}
        </Box>
      )}

      {domainEntries.length > 0 && (
        <Box paddingX={0}>
          <Text color={theme.border}>{'─'.repeat(60)}</Text>
        </Box>
      )}

      {/* Live feed */}
      <Box flexDirection="column" paddingX={1} paddingY={0}>
        <Text color={theme.textDim} bold>LIVE FEED</Text>
        {visibleFeed.length === 0 && (
          <Text color={theme.textDim}>Waiting for events...</Text>
        )}
        {visibleFeed.map(item => {
          const icon = feedIcon(item);
          const iconColor = feedIconColor(item);
          const maxW = 52;
          const text = item.text.length > maxW ? item.text.slice(0, maxW - 1) + '…' : item.text;
          return (
            <Box key={item.id} gap={1}>
              <Text color={iconColor}>{icon}</Text>
              <Box flexGrow={1}>
                <Text color={theme.text}>{text}</Text>
              </Box>
              {item.domain && <Text color={theme.textDim}>{item.domain}</Text>}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
