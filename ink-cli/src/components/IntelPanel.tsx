import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { IntelState, FeedItem } from '../hooks/useIntel.js';

interface Props {
  intel: IntelState;
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
  return '\u2588'.repeat(filled) + '\u2591'.repeat(BAR_WIDTH - filled);
}

function feedIcon(item: FeedItem): string {
  switch (item.type) {
    case 'reading':     return '[r]';
    case 'finding':     return '[f]';
    case 'gap':         return '[!]';
    case 'improvement': return '[v]';
    case 'round_start': return '[>]';
    case 'round_done':  return '[>]';
    case 'scores':      return '[s]';
    default:            return '[ ]';
  }
}

function feedIconColor(item: FeedItem): string {
  switch (item.type) {
    case 'reading':     return theme.textDim;
    case 'finding':     return theme.cyan;
    case 'gap':
      if (item.impact === 'high') return theme.red;
      if (item.impact === 'medium') return theme.yellow;
      return theme.yellow;
    case 'improvement': return theme.green;
    case 'round_start': return theme.accent;
    case 'round_done':  return theme.accent;
    case 'scores':      return theme.accentBright;
    default:            return theme.textDim;
  }
}

export default function IntelPanel({ intel }: Props) {
  const { running, round, overallScore, totalImprovements, scores, feed } = intel;

  const statusText = running ? 'Running' : 'Stopped';
  const statusColor = running ? theme.green : theme.textMuted;
  const roundText = round > 0 ? `Round ${round}` : 'Idle';

  const domainEntries = Object.entries(scores);

  // Visible feed slice (last FEED_VISIBLE)
  const visibleFeed = feed.slice(-FEED_VISIBLE);

  if (!running && round === 0) {
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
          <Text color={theme.accent} bold>◆ PromptIntel</Text>
          <Text color={theme.textDim}>—</Text>
          <Text color={theme.textMuted}>Press /intel to start</Text>
        </Box>
      </Box>
    );
  }

  return (
    <Box
      borderStyle="round"
      borderColor={running ? theme.accent : theme.border}
      flexDirection="column"
      marginX={1}
    >
      {/* Header row */}
      <Box paddingX={1} gap={2}>
        <Text color={theme.accentBright} bold>◆ PromptIntel</Text>
        <Text color={theme.textDim}>—</Text>
        <Text color={theme.textMuted}>{roundText}</Text>
        <Text color={theme.textDim}>—</Text>
        <Text color={statusColor}>{statusText}</Text>
      </Box>

      {/* Stats row */}
      <Box paddingX={1} gap={2} borderStyle="single" borderColor={theme.border} borderTop={false} borderLeft={false} borderRight={false}>
        <Text color={theme.textBright}>
          Overall CC Parity: <Text color={scoreColor(overallScore)} bold>{overallScore}%</Text>
        </Text>
        <Text color={theme.textDim}>|</Text>
        <Text color={theme.textMuted}>
          {totalImprovements} improvement{totalImprovements !== 1 ? 's' : ''} applied
        </Text>
        <Text color={theme.textDim}>|</Text>
        <Text color={theme.textMuted}>Round {round}/{'\u221e'}</Text>
      </Box>

      {/* Domain scores */}
      {domainEntries.length > 0 && (
        <Box flexDirection="column" paddingX={1} paddingY={0}>
          <Text color={theme.textDim} bold>DOMAIN SCORES</Text>
          {domainEntries.map(([domain, score]) => {
            const bar = progressBar(score);
            const col = scoreColor(score);
            const label = domain.padEnd(20);
            return (
              <Box key={domain} gap={1}>
                <Text color={theme.textMuted}>{label}</Text>
                <Text color={col}>{bar}</Text>
                <Text color={col} bold>{score}%</Text>
              </Box>
            );
          })}
        </Box>
      )}

      {/* Divider */}
      {domainEntries.length > 0 && (
        <Box paddingX={0}>
          <Text color={theme.border}>{'\u2500'.repeat(60)}</Text>
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
          const maxTextWidth = 52;
          const text = item.text.length > maxTextWidth
            ? item.text.slice(0, maxTextWidth - 1) + '\u2026'
            : item.text;

          return (
            <Box key={item.id} gap={1}>
              <Text color={iconColor}>{icon}</Text>
              <Box flexGrow={1}>
                <Text color={theme.text}>{text}</Text>
              </Box>
              {item.domain && (
                <Text color={theme.textDim}>{item.domain}</Text>
              )}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
