import React, { useState, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import Spinner from 'ink-spinner';
import { theme } from '../theme.js';
import type {
  PluginsState,
  HubPlugin,
  AuditResult,
} from '../hooks/usePlugins.js';
import { QualityReport } from './QualityReport.js';

type Step = 'inspect' | 'permissions' | 'audit' | 'confirm' | 'done';

interface Props {
  plugin: HubPlugin;
  plugins: PluginsState;
  onClose: () => void;
}

export function InstallWizard({ plugin, plugins, onClose }: Props) {
  const [step, setStep] = useState<Step>('inspect');
  const [permsGranted, setPermsGranted] = useState<Set<string>>(new Set());
  const [permIdx, setPermIdx] = useState(0);
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [auditing, setAuditing] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Auto-fire audit on entering 'audit' step
  useEffect(() => {
    if (step === 'audit' && audit === null && !auditing) {
      setAuditing(true);
      plugins
        .runAudit(plugin.name, plugin.version)
        .then(r => setAudit(r))
        .catch(e => setError(String(e)))
        .finally(() => setAuditing(false));
    }
  }, [step, audit, auditing, plugin.name, plugin.version, plugins]);

  const togglePerm = (p: string) => {
    setPermsGranted(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  useInput((input, key) => {
    if (step === 'done') {
      if (key.escape || key.return) onClose();
      return;
    }
    if (key.escape) {
      onClose();
      return;
    }

    if (step === 'inspect') {
      if (key.return) setStep(plugin.permissions.length > 0 ? 'permissions' : 'audit');
      return;
    }
    if (step === 'permissions') {
      if (key.upArrow) setPermIdx(i => Math.max(0, i - 1));
      else if (key.downArrow) setPermIdx(i => Math.min(plugin.permissions.length - 1, i + 1));
      else if (input === ' ') togglePerm(plugin.permissions[permIdx]);
      else if (key.return) setStep('audit');
      return;
    }
    if (step === 'audit') {
      if (key.return && audit && !audit.blocked) setStep('confirm');
      return;
    }
    if (step === 'confirm') {
      if (key.return && !installing) {
        setInstalling(true);
        plugins
          .install(plugin.name, Array.from(permsGranted), plugin.version, false)
          .then(() => setStep('done'))
          .catch(e => {
            setError(String(e));
            setInstalling(false);
          });
      }
      return;
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.accent} padding={1}>
      <Text bold color={theme.accentBright}>
        Install Wizard — {plugin.name}@{plugin.version}
      </Text>
      <Text color={theme.textDim}>
        Step {['inspect', 'permissions', 'audit', 'confirm', 'done'].indexOf(step) + 1}/5 — {step}
      </Text>

      {step === 'inspect' && (
        <Box flexDirection="column" marginTop={1}>
          <Text>{plugin.description || '(no description)'}</Text>
          <Text color={theme.textDim}>
            Author: {plugin.author || '—'}  ·  {plugin.tool_count} tools  ·  {Math.round(plugin.size_bytes / 1024)} KB
          </Text>
          {plugin.tags.length > 0 && (
            <Text color={theme.textDim}>Tags: {plugin.tags.join(', ')}</Text>
          )}
          <Text color={theme.textDim}>sha256: {plugin.sha256.slice(0, 16)}…</Text>
          <Box marginTop={1}>
            <Text color={theme.accent}>Press Enter to continue, Esc to cancel.</Text>
          </Box>
        </Box>
      )}

      {step === 'permissions' && (
        <Box flexDirection="column" marginTop={1}>
          <Text>Plugin requests these permissions. Space to toggle, Enter to continue.</Text>
          {plugin.permissions.map((p, i) => (
            <Text
              key={p}
              color={i === permIdx ? theme.accentBright : theme.text}
            >
              {i === permIdx ? '> ' : '  '}[{permsGranted.has(p) ? 'x' : ' '}] {p}
            </Text>
          ))}
          <Box marginTop={1}>
            <Text color={theme.textDim}>
              Granted: {Array.from(permsGranted).join(', ') || '(none — runtime will deny those capabilities)'}
            </Text>
          </Box>
        </Box>
      )}

      {step === 'audit' && (
        <Box flexDirection="column" marginTop={1}>
          {auditing && (
            <Text color={theme.accent}>
              <Spinner type="dots" /> running audit…
            </Text>
          )}
          {audit && <QualityReport name={plugin.name} report={audit} />}
          {audit && !audit.blocked && (
            <Box marginTop={1}>
              <Text color={theme.green}>✓ Safe to install. Press Enter to continue.</Text>
            </Box>
          )}
          {audit && audit.blocked && (
            <Box marginTop={1}>
              <Text color={theme.red}>
                Install blocked. Press Esc to cancel.
              </Text>
            </Box>
          )}
        </Box>
      )}

      {step === 'confirm' && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold>Ready to install:</Text>
          <Text>  name: {plugin.name}@{plugin.version}</Text>
          <Text>  permissions: {Array.from(permsGranted).join(', ') || '(none)'}</Text>
          <Text>  audit score: {audit?.score}/100</Text>
          <Box marginTop={1}>
            {installing ? (
              <Text color={theme.accent}>
                <Spinner type="dots" /> installing…
              </Text>
            ) : (
              <Text color={theme.green}>Press Enter to confirm, Esc to cancel.</Text>
            )}
          </Box>
        </Box>
      )}

      {step === 'done' && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.green} bold>
            ✓ {plugin.name}@{plugin.version} installed.
          </Text>
          <Text color={theme.textDim}>Press Enter or Esc to close.</Text>
        </Box>
      )}

      {error && (
        <Box marginTop={1}>
          <Text color={theme.red}>⚠ {error}</Text>
        </Box>
      )}
    </Box>
  );
}
