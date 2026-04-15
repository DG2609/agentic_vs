import { useState, useEffect, useRef, useCallback } from 'react';
import { spawn, ChildProcess } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import type { ServerState } from '../types.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
// ink-cli/src/hooks/ → up 3 levels → project root
const PROJECT_ROOT = join(__dirname, '..', '..', '..');
const BACKEND_URL = 'http://localhost:8000';

async function pollHealth(maxMs = 30000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    try {
      const res = await fetch(`${BACKEND_URL}/health`);
      if (res.ok) return true;
    } catch {
      // not ready yet
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

export function useServer() {
  const [state, setState] = useState<ServerState>({
    isReady: false,
    isStarting: true,
    error: null,
    pid: null,
  });
  const procRef = useRef<ChildProcess | null>(null);

  const stop = useCallback(() => {
    if (procRef.current && !procRef.current.killed) {
      procRef.current.kill('SIGTERM');
      procRef.current = null;
    }
  }, []);

  useEffect(() => {
    // Check if backend already running first
    pollHealth(2000).then(already => {
      if (already) {
        setState({ isReady: true, isStarting: false, error: null, pid: null });
        return;
      }

      // Spawn the Python backend
      const proc = spawn('python', ['server/main.py'], {
        cwd: PROJECT_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          ...process.env,
          // Force UTF-8 output so Windows charmap doesn't crash on emoji
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
        },
      });

      procRef.current = proc;
      setState(s => ({ ...s, pid: proc.pid ?? null }));

      // Collect stderr for error reporting
      const stderrChunks: Buffer[] = [];
      proc.stderr?.on('data', (chunk: Buffer) => stderrChunks.push(chunk));

      proc.on('error', (err) => {
        setState({ isReady: false, isStarting: false, error: `Failed to start server: ${err.message}`, pid: null });
      });

      proc.on('exit', (code) => {
        if (code !== 0 && code !== null) {
          const errText = Buffer.concat(stderrChunks).toString('utf-8').slice(-400);
          setState(s => ({ ...s, isReady: false, isStarting: false, error: `Server exited (code ${code}):\n${errText}` }));
        }
      });

      // Poll until backend is healthy
      pollHealth(30000).then(ready => {
        if (ready) {
          setState(s => ({ ...s, isReady: true, isStarting: false }));
        } else {
          const errText = Buffer.concat(stderrChunks).toString('utf-8').slice(-400);
          setState({
            isReady: false, isStarting: false, pid: null,
            error: errText
              ? `Server failed to start:\n${errText}`
              : 'Server did not respond within 30s — check Python + dependencies',
          });
        }
      });
    });

    return () => stop();
  }, [stop]);

  return { ...state, stop };
}
