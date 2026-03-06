/**
 * shadowdevClient.ts — Communication layer between the VS Code extension
 * and the ShadowDev agent.
 *
 * Supports two modes (configured via shadowdev.mode):
 *   cli    — spawn `python cli.py --prompt "..." --output-format json`
 *   server — POST to `http://localhost:8000/api/run`
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";

export interface AgentRequest {
  prompt: string;
  sessionId?: string;
  allowedTools?: string[];
  timeout?: number;
}

export interface AgentResponse {
  response: string;
  session_id?: string;
  error?: string;
  modified_files?: string[];
}

function getConfig() {
  const cfg = vscode.workspace.getConfiguration("shadowdev");
  return {
    pythonPath: cfg.get<string>("pythonPath", "python"),
    workspacePath:
      cfg.get<string>("workspacePath", "") ||
      vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ||
      "",
    mode: cfg.get<"cli" | "server">("mode", "cli"),
    serverUrl: cfg.get<string>("serverUrl", "http://localhost:8000"),
    sessionId: cfg.get<string>("sessionId", ""),
  };
}

// ── CLI mode ──────────────────────────────────────────────────

function findCliPath(workspacePath: string): string {
  // Look for cli.py relative to the workspace, then fall back to PATH
  return path.join(workspacePath, "cli.py");
}

export async function runAgentCli(
  req: AgentRequest,
  onChunk?: (chunk: string) => void
): Promise<AgentResponse> {
  const cfg = getConfig();
  const cliPath = findCliPath(cfg.workspacePath);

  const args: string[] = [
    cliPath,
    "--prompt",
    req.prompt,
    "--output-format",
    onChunk ? "stream-json" : "json",
  ];

  if (req.sessionId || cfg.sessionId) {
    args.push("--session-id", req.sessionId || cfg.sessionId);
  }
  if (req.timeout) {
    args.push("--timeout", String(req.timeout));
  }
  if (req.allowedTools?.length) {
    args.push("--allowed-tools", req.allowedTools.join(","));
  }

  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";

    const proc = cp.spawn(cfg.pythonPath, args, {
      cwd: cfg.workspacePath,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    proc.stdout.on("data", (data: Buffer) => {
      const chunk = data.toString("utf-8");
      stdout += chunk;

      // Stream mode: emit each JSON line as it arrives
      if (onChunk) {
        for (const line of chunk.split("\n").filter((l) => l.trim())) {
          try {
            const parsed = JSON.parse(line);
            if (parsed.delta || parsed.response) {
              onChunk(parsed.delta || parsed.response);
            }
          } catch {
            // not a JSON line — skip
          }
        }
      }
    });

    proc.stderr.on("data", (data: Buffer) => {
      stderr += data.toString("utf-8");
    });

    proc.on("close", (code) => {
      if (code === 124) {
        resolve({ response: "", error: "⏱️ Agent timed out." });
        return;
      }

      try {
        const parsed: AgentResponse = JSON.parse(stdout.trim());
        resolve(parsed);
      } catch {
        // Not JSON — return raw stdout
        resolve({
          response: stdout.trim() || `Agent exited with code ${code}`,
          error: stderr.trim() || undefined,
        });
      }
    });

    proc.on("error", (err) => {
      resolve({
        response: "",
        error: `Failed to start agent: ${err.message}\n\nCheck shadowdev.pythonPath setting.`,
      });
    });
  });
}

// ── Server mode ───────────────────────────────────────────────

export async function runAgentServer(
  req: AgentRequest
): Promise<AgentResponse> {
  const cfg = getConfig();
  const url = `${cfg.serverUrl.replace(/\/$/, "")}/api/run`;

  try {
    const body = JSON.stringify({
      prompt: req.prompt,
      session_id: req.sessionId || cfg.sessionId || undefined,
      timeout: req.timeout,
    });

    // Use built-in fetch (Node 18+) or fall back to http module
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout((req.timeout || 120) * 1000 + 5000),
    });

    if (!res.ok) {
      const text = await res.text();
      return { response: "", error: `Server error ${res.status}: ${text}` };
    }

    return (await res.json()) as AgentResponse;
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { response: "", error: `Server request failed: ${msg}` };
  }
}

// ── Unified entry point ───────────────────────────────────────

export async function runAgent(
  req: AgentRequest,
  onChunk?: (chunk: string) => void
): Promise<AgentResponse> {
  const { mode } = getConfig();
  if (mode === "server") {
    return runAgentServer(req);
  }
  return runAgentCli(req, onChunk);
}
