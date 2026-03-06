/**
 * preload.js — Context bridge between the renderer and main process.
 *
 * Exposes a minimal, typed API to the web UI via window.shadowdev.
 * All methods use ipcRenderer.invoke() (async, two-way) or
 * ipcRenderer.on() (push events from main → renderer).
 *
 * Security: contextIsolation is ON, nodeIntegration is OFF.
 * Only explicitly listed channels are exposed.
 */

"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// Allowed IPC channels (invoke — request/response)
const INVOKE_CHANNELS = new Set([
  "sd:open-file-dialog",
  "sd:open-folder-dialog",
  "sd:save-file-dialog",
  "sd:show-item-in-folder",
  "sd:get-app-version",
  "sd:get-server-port",
  "sd:check-for-updates",
  "sd:get-log-path",
]);

// Allowed IPC channels (push events from main to renderer)
const EVENT_CHANNELS = new Set([
  "sd:update-available",
  "sd:update-downloaded",
  "sd:update-error",
  "sd:server-status",
]);

contextBridge.exposeInMainWorld("shadowdev", {
  // ── Invoke (request → response) ────────────────────────────

  /**
   * Open a native file picker.
   * @param {Electron.OpenDialogOptions} options
   * @returns {Promise<{canceled: boolean, filePaths: string[]}>}
   */
  openFileDialog: (options = {}) => {
    if (!INVOKE_CHANNELS.has("sd:open-file-dialog")) return Promise.reject();
    return ipcRenderer.invoke("sd:open-file-dialog", options);
  },

  /**
   * Open a native folder picker.
   * @returns {Promise<{canceled: boolean, filePaths: string[]}>}
   */
  openFolderDialog: () =>
    ipcRenderer.invoke("sd:open-folder-dialog"),

  /**
   * Open a native save dialog.
   * @param {Electron.SaveDialogOptions} options
   * @returns {Promise<{canceled: boolean, filePath?: string}>}
   */
  saveFileDialog: (options = {}) =>
    ipcRenderer.invoke("sd:save-file-dialog", options),

  /**
   * Highlight a file in the OS file manager.
   * @param {string} filePath
   */
  showItemInFolder: (filePath) =>
    ipcRenderer.invoke("sd:show-item-in-folder", filePath),

  /** @returns {Promise<string>} Semantic version string, e.g. "0.4.0" */
  getAppVersion: () => ipcRenderer.invoke("sd:get-app-version"),

  /** @returns {Promise<number>} Port the Python server is listening on */
  getServerPort: () => ipcRenderer.invoke("sd:get-server-port"),

  /** Trigger an update check manually. */
  checkForUpdates: () => ipcRenderer.invoke("sd:check-for-updates"),

  /** @returns {Promise<string>} Path to the electron-log file */
  getLogPath: () => ipcRenderer.invoke("sd:get-log-path"),

  // ── Event listeners ─────────────────────────────────────────

  /**
   * Subscribe to a main-process push event.
   * @param {string} channel  One of the EVENT_CHANNELS constants
   * @param {function} handler
   * @returns {function} Unsubscribe function
   */
  on: (channel, handler) => {
    if (!EVENT_CHANNELS.has(channel)) {
      console.warn("[preload] Unknown event channel:", channel);
      return () => {};
    }
    const wrapped = (_event, ...args) => handler(...args);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  },

  // ── Constants ───────────────────────────────────────────────
  IS_DESKTOP: true,
  platform: process.platform,
});
