import { useState, useEffect, useCallback } from 'react';
import type { Socket } from 'socket.io-client';

const BACKEND = 'http://localhost:8000';

export interface InstalledPlugin {
  name: string;
  version: string;
  status: 'installed' | 'disabled' | 'error';
  score: number;
  permissions: string[];
  install_path: string;
  installed_at: string;
  last_audited_at: string;
  last_error: string | null;
}

export interface HubPlugin {
  name: string;
  version: string;
  url: string;
  sha256: string;
  author: string;
  description: string;
  category: string;
  tags: string[];
  permissions: string[];
  tool_count: number;
  size_bytes: number;
  signature: string | null;
}

export interface AuditIssue {
  rule: string;
  message: string;
  severity: 'high' | 'medium' | 'low';
  file: string;
  line: number;
}

export interface AuditResult {
  score: number;
  blocked: boolean;
  issues: AuditIssue[];
  blockers: AuditIssue[];
}

export interface PluginsState {
  installed: InstalledPlugin[];
  hubResults: HubPlugin[];
  audit: Record<string, AuditResult>;
  lastError: string | null;
  refreshInstalled: () => Promise<void>;
  searchHub: (query: string, category?: string) => Promise<HubPlugin[]>;
  runAudit: (name: string, version?: string) => Promise<AuditResult>;
  install: (
    name: string,
    permissions: string[],
    version?: string,
    force?: boolean,
  ) => Promise<InstalledPlugin>;
  uninstall: (name: string) => Promise<void>;
}

export function usePlugins(socket: Socket | null): PluginsState {
  const [installed, setInstalled] = useState<InstalledPlugin[]>([]);
  const [hubResults, setHubResults] = useState<HubPlugin[]>([]);
  const [audit, setAudit] = useState<Record<string, AuditResult>>({});
  const [lastError, setLastError] = useState<string | null>(null);

  const refreshInstalled = useCallback(async () => {
    try {
      const r = await fetch(`${BACKEND}/api/plugins`);
      const data = await r.json();
      setInstalled(data.plugins || []);
    } catch (e) {
      setLastError(String(e));
    }
  }, []);

  const searchHub = useCallback(
    async (query: string, category?: string): Promise<HubPlugin[]> => {
      try {
        const params = new URLSearchParams({ q: query });
        if (category) params.set('category', category);
        const r = await fetch(`${BACKEND}/api/plugins/search?${params}`);
        const data = await r.json();
        const results: HubPlugin[] = data.results || [];
        setHubResults(results);
        return results;
      } catch (e) {
        setLastError(String(e));
        return [];
      }
    },
    [],
  );

  const runAudit = useCallback(
    async (name: string, version?: string): Promise<AuditResult> => {
      const r = await fetch(`${BACKEND}/api/plugins/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, version }),
      });
      const data = await r.json();
      if (!r.ok) {
        setLastError(data.error || `audit failed (${r.status})`);
        throw new Error(data.error || `audit failed (${r.status})`);
      }
      setAudit(prev => ({ ...prev, [name]: data }));
      return data;
    },
    [],
  );

  const install = useCallback(
    async (
      name: string,
      permissions: string[],
      version?: string,
      force = false,
    ): Promise<InstalledPlugin> => {
      const r = await fetch(`${BACKEND}/api/plugins/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, version, permissions, force }),
      });
      const data = await r.json();
      if (!r.ok) {
        setLastError(data.error || `install failed (${r.status})`);
        throw new Error(data.error || `install failed (${r.status})`);
      }
      await refreshInstalled();
      return data;
    },
    [refreshInstalled],
  );

  const uninstall = useCallback(
    async (name: string): Promise<void> => {
      const r = await fetch(`${BACKEND}/api/plugins/uninstall`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await r.json();
      if (!r.ok) {
        setLastError(data.error || `uninstall failed (${r.status})`);
        throw new Error(data.error || `uninstall failed (${r.status})`);
      }
      await refreshInstalled();
    },
    [refreshInstalled],
  );

  useEffect(() => {
    refreshInstalled();
  }, [refreshInstalled]);

  useEffect(() => {
    if (!socket) return;

    const onInstalled = (_data: InstalledPlugin) => {
      refreshInstalled();
    };
    const onUninstalled = (_data: { name: string }) => {
      refreshInstalled();
    };
    const onError = (data: { op: string; name: string; error: string }) => {
      setLastError(`[${data.op}] ${data.name}: ${data.error}`);
    };
    const onAuditDone = (data: { name: string; report: AuditResult }) => {
      setAudit(prev => ({ ...prev, [data.name]: data.report }));
    };

    socket.on('plugins:installed', onInstalled);
    socket.on('plugins:uninstalled', onUninstalled);
    socket.on('plugins:error', onError);
    socket.on('plugins:audit_done', onAuditDone);

    return () => {
      socket.off('plugins:installed', onInstalled);
      socket.off('plugins:uninstalled', onUninstalled);
      socket.off('plugins:error', onError);
      socket.off('plugins:audit_done', onAuditDone);
    };
  }, [socket, refreshInstalled]);

  return {
    installed,
    hubResults,
    audit,
    lastError,
    refreshInstalled,
    searchHub,
    runAudit,
    install,
    uninstall,
  };
}
