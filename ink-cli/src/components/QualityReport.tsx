import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { AuditResult, AuditIssue } from '../hooks/usePlugins.js';

interface Props {
  name: string;
  report: AuditResult;
  compact?: boolean;
}

function scoreColor(score: number): string {
  if (score < 40) return theme.red;
  if (score < 70) return theme.yellow;
  return theme.green;
}

function severityColor(sev: AuditIssue['severity']): string {
  if (sev === 'high') return theme.red;
  if (sev === 'medium') return theme.yellow;
  return theme.textMuted;
}

export function QualityReport({ name, report, compact = false }: Props) {
  const sc = report.score;
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        <Text bold>Audit: {name}  </Text>
        <Text color={scoreColor(sc)}>score {sc}/100</Text>
        {report.blocked && <Text color={theme.red} bold>  BLOCKED</Text>}
      </Box>

      {report.blockers.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.red} bold>Blockers ({report.blockers.length})</Text>
          {report.blockers.slice(0, compact ? 3 : 20).map((b, i) => (
            <Text key={i} color={severityColor(b.severity)}>
              • [{b.rule}] {b.message}
              {b.file ? `  @ ${b.file}${b.line > 0 ? ':' + b.line : ''}` : ''}
            </Text>
          ))}
        </Box>
      )}

      {report.issues.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.yellow} bold>Warnings ({report.issues.length})</Text>
          {report.issues.slice(0, compact ? 3 : 20).map((b, i) => (
            <Text key={i} color={severityColor(b.severity)}>
              • [{b.rule}] {b.message}
              {b.file ? `  @ ${b.file}${b.line > 0 ? ':' + b.line : ''}` : ''}
            </Text>
          ))}
        </Box>
      )}

      {report.blockers.length === 0 && report.issues.length === 0 && (
        <Text color={theme.green}>✓ No issues found.</Text>
      )}
    </Box>
  );
}
