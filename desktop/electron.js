/**
 * electron.js — ShadowDev Desktop App main process.
 *
 * Responsibilities:
 *   1. Find/start the Python backend server (main.py)
 *   2. Serve the Next.js web IDE (static build or dev server)
 *   3. Open the main BrowserWindow
 *   4. Manage system tray (hide/show, quit)
 *   5. Handle auto-updates via electron-updater
 *   6. Register IPC handlers for native features
 */

"use strict";

const { app, BrowserWindow, shell, dialog, ipcMain } = require("electron");
const path = require("path");
const { setupTray } = require("./src/tray");
const { setupUpdater } = require("./src/updater");
const { registerIpcHandlers } = require("./src/ipc-handlers");
const { startPythonServer, stopPythonServer } = require("./src/python-server");

const log = require("electron-log");
log.transports.file.level = "info";
log.transports.console.level = "debug";

// ── Constants ─────────────────────────────────────────────────

const IS_DEV = process.env.NODE_ENV === "development";
const IS_MAC = process.platform === "darwin";

// Port the Python server will listen on (falls back to env var)
const SERVER_PORT = parseInt(process.env.SHADOWDEV_PORT || "8000", 10);

// When dev: point to the Next.js dev server; when prod: serve built files
const WEB_URL = IS_DEV
  ? `http://localhost:3000`
  : `http://localhost:${SERVER_PORT}`;

// ── Window management ─────────────────────────────────────────

/** @type {BrowserWindow | null} */
let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: "ShadowDev",
    icon: path.join(__dirname, "assets", "icon.png"),
    show: false,  // shown after ready-to-show
    backgroundColor: "#1e1e1e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
    titleBarStyle: IS_MAC ? "hiddenInset" : "default",
  });

  // Load the web UI
  mainWindow.loadURL(WEB_URL).catch((err) => {
    log.error("[main] Failed to load web UI:", err.message);
    showLoadError(err.message);
  });

  // Show once ready — avoids white flash
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (IS_DEV) {
      mainWindow.webContents.openDevTools();
    }
  });

  // Open external links in the default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  // Hide to tray instead of closing (Windows/Linux)
  mainWindow.on("close", (e) => {
    if (!app.isQuitting && !IS_MAC) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  return mainWindow;
}

function showLoadError(message) {
  dialog.showErrorBox(
    "ShadowDev — Failed to Load",
    `Could not connect to the ShadowDev server.\n\n${message}\n\n` +
      `Make sure Python is installed and requirements are met.\n` +
      `Check the log at: ${log.transports.file.getFile().path}`
  );
}

/** Bring the main window to front, creating it if needed. */
function showWindow() {
  if (!mainWindow) {
    createWindow();
  } else if (mainWindow.isMinimized()) {
    mainWindow.restore();
  } else {
    mainWindow.show();
  }
  mainWindow.focus();
}

// ── App lifecycle ─────────────────────────────────────────────

// Single instance lock — prevent multiple desktop instances
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showWindow();
  });
}

app.whenReady().then(async () => {
  log.info("[main] App ready. IS_DEV=%s PORT=%d", IS_DEV, SERVER_PORT);

  // Start Python backend (skipped in dev — user starts it manually)
  if (!IS_DEV) {
    try {
      await startPythonServer(SERVER_PORT);
      log.info("[main] Python server started on port", SERVER_PORT);
    } catch (err) {
      log.error("[main] Python server failed to start:", err.message);
      dialog.showErrorBox(
        "ShadowDev — Server Error",
        `Failed to start the ShadowDev Python server:\n\n${err.message}`
      );
      app.quit();
      return;
    }
  }

  // IPC bridge
  registerIpcHandlers(ipcMain, { serverPort: SERVER_PORT });

  // Create window
  createWindow();

  // System tray
  setupTray({ showWindow, app });

  // Auto-updater (only in packaged builds)
  if (!IS_DEV && app.isPackaged) {
    setupUpdater(mainWindow);
  }

  // macOS: re-open window on dock click
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else {
      showWindow();
    }
  });
});

app.on("window-all-closed", () => {
  // macOS keeps app running until explicit Cmd+Q
  if (!IS_MAC) {
    app.quit();
  }
});

app.on("before-quit", () => {
  app.isQuitting = true;
  log.info("[main] App quitting — stopping Python server");
  stopPythonServer();
});

// Security: prevent navigation to arbitrary URLs
app.on("web-contents-created", (_event, contents) => {
  contents.on("will-navigate", (event, navigationUrl) => {
    const allowedOrigins = [
      `http://localhost:${SERVER_PORT}`,
      "http://localhost:3000",
    ];
    const { origin } = new URL(navigationUrl);
    if (!allowedOrigins.includes(origin)) {
      log.warn("[security] Blocked navigation to:", navigationUrl);
      event.preventDefault();
    }
  });
});
