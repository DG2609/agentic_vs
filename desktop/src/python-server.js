/**
 * python-server.js — Spawn and manage the ShadowDev Python backend.
 *
 * In production (packaged) builds the Python server lives in
 * resources/shadowdev/ (copied via electron-builder extraResources).
 * The frontend static build is in resources/web/.
 *
 * Flow:
 *   1. startPythonServer(port) — spawns `python main.py`
 *   2. Polls http://localhost:<port>/health until ready (max 30s)
 *   3. Returns when server is reachable
 *   4. stopPythonServer() — SIGTERM the child process on app quit
 */

"use strict";

const { spawn } = require("child_process");
const path = require("path");
const { app } = require("electron");
const log = require("electron-log");

/** @type {import('child_process').ChildProcess | null} */
let serverProcess = null;

const STARTUP_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 500;

/**
 * Resolve the path to the bundled Python executable.
 * On packaged builds, look for a venv inside the resources folder.
 * Fall back to the system `python`/`python3`.
 */
function findPython() {
  if (app.isPackaged) {
    const resourcesDir = process.resourcesPath;
    const candidates = [
      // Windows
      path.join(resourcesDir, "shadowdev", ".venv", "Scripts", "python.exe"),
      // macOS / Linux
      path.join(resourcesDir, "shadowdev", ".venv", "bin", "python3"),
      path.join(resourcesDir, "shadowdev", ".venv", "bin", "python"),
    ];
    for (const c of candidates) {
      try {
        require("fs").accessSync(c);
        return c;
      } catch {
        // not found
      }
    }
  }
  // Dev mode or no bundled venv — use system Python
  return process.platform === "win32" ? "python" : "python3";
}

/**
 * Resolve the path to the `main.py` entry point.
 */
function findMainPy() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "shadowdev", "main.py");
  }
  // Dev: main.py is in the project root (one level up from desktop/)
  return path.join(__dirname, "..", "..", "main.py");
}

/**
 * Poll the server's health endpoint until it responds or times out.
 * @param {number} port
 * @param {number} timeoutMs
 * @returns {Promise<void>}
 */
async function waitForServer(port, timeoutMs) {
  const url = `http://localhost:${port}/health`;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    try {
      // Use built-in fetch (Node 18+)
      const res = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (res.ok) {
        return;
      }
    } catch {
      // Not up yet — wait and retry
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  throw new Error(
    `Server did not become ready within ${timeoutMs / 1000}s. ` +
      `Check the log for Python errors.`
  );
}

/**
 * Spawn the Python backend and wait for it to become ready.
 * @param {number} port  Port for the Python server to listen on
 * @returns {Promise<void>}
 */
async function startPythonServer(port) {
  const python = findPython();
  const mainPy = findMainPy();

  log.info("[python-server] Starting:", python, mainPy, "--port", port);

  serverProcess = spawn(python, [mainPy, "--port", String(port)], {
    cwd: path.dirname(mainPy),
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      SHADOWDEV_PORT: String(port),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  serverProcess.stdout.on("data", (data) => {
    log.info("[python]", data.toString("utf-8").trimEnd());
  });

  serverProcess.stderr.on("data", (data) => {
    log.warn("[python:stderr]", data.toString("utf-8").trimEnd());
  });

  serverProcess.on("error", (err) => {
    log.error("[python-server] Spawn error:", err.message);
  });

  serverProcess.on("exit", (code, signal) => {
    log.info("[python-server] Exited. code=%s signal=%s", code, signal);
    serverProcess = null;
  });

  // Wait until the /health endpoint responds
  await waitForServer(port, STARTUP_TIMEOUT_MS);
}

/**
 * Gracefully terminate the Python server.
 */
function stopPythonServer() {
  if (!serverProcess) return;

  log.info("[python-server] Stopping (PID=%d)", serverProcess.pid);

  try {
    serverProcess.kill("SIGTERM");

    // Force-kill after 5s if it doesn't exit
    const killer = setTimeout(() => {
      if (serverProcess) {
        log.warn("[python-server] Force-killing after 5s");
        serverProcess.kill("SIGKILL");
      }
    }, 5000);

    serverProcess.once("exit", () => clearTimeout(killer));
  } catch (err) {
    log.error("[python-server] Error during stop:", err.message);
  }
}

/** Return true if the server process is currently running. */
function isServerRunning() {
  return serverProcess !== null && !serverProcess.killed;
}

module.exports = { startPythonServer, stopPythonServer, isServerRunning };
