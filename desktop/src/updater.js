/**
 * updater.js — Auto-update support via electron-updater.
 *
 * Checks GitHub Releases for new versions on startup (after a 5s delay)
 * and whenever the user triggers a manual check.
 *
 * Events emitted to renderer via mainWindow.webContents:
 *   sd:update-available  { version, releaseNotes }
 *   sd:update-downloaded { version, releaseNotes }
 *   sd:update-error      { message }
 */

"use strict";

const { autoUpdater } = require("electron-updater");
const log = require("electron-log");

// Use electron-log for updater output
autoUpdater.logger = log;
autoUpdater.logger.transports.file.level = "info";

// Don't auto-install — let the user decide when to restart
autoUpdater.autoInstallOnAppQuit = true;
autoUpdater.autoDownload = true;

/** @type {Electron.BrowserWindow | null} */
let _mainWindow = null;

function emit(channel, payload) {
  if (_mainWindow && !_mainWindow.isDestroyed()) {
    _mainWindow.webContents.send(channel, payload);
  }
}

/**
 * Initialize the auto-updater.
 * @param {Electron.BrowserWindow} mainWindow
 */
function setupUpdater(mainWindow) {
  _mainWindow = mainWindow;

  autoUpdater.on("checking-for-update", () => {
    log.info("[updater] Checking for updates…");
  });

  autoUpdater.on("update-available", (info) => {
    log.info("[updater] Update available:", info.version);
    emit("sd:update-available", {
      version: info.version,
      releaseNotes: info.releaseNotes || "",
    });
  });

  autoUpdater.on("update-not-available", () => {
    log.info("[updater] Already on latest version");
  });

  autoUpdater.on("error", (err) => {
    log.error("[updater] Error:", err.message);
    emit("sd:update-error", { message: err.message });
  });

  autoUpdater.on("download-progress", (progress) => {
    log.info(
      "[updater] Download progress: %d% (%s/s)",
      Math.round(progress.percent),
      _formatBytes(progress.bytesPerSecond)
    );
  });

  autoUpdater.on("update-downloaded", (info) => {
    log.info("[updater] Update downloaded:", info.version);
    emit("sd:update-downloaded", {
      version: info.version,
      releaseNotes: info.releaseNotes || "",
    });
  });

  // Check after a short delay so the app has time to fully load
  setTimeout(() => {
    autoUpdater.checkForUpdatesAndNotify().catch((err) => {
      log.warn("[updater] Check failed:", err.message);
    });
  }, 5000);
}

/** Trigger a manual update check. */
async function checkForUpdates() {
  try {
    const result = await autoUpdater.checkForUpdates();
    return result ? { version: result.updateInfo.version } : null;
  } catch (err) {
    log.error("[updater] Manual check failed:", err.message);
    throw err;
  }
}

/** Install a downloaded update and restart. */
function quitAndInstall() {
  autoUpdater.quitAndInstall(false, true);
}

function _formatBytes(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

module.exports = { setupUpdater, checkForUpdates, quitAndInstall };
