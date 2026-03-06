/**
 * ipc-handlers.js — Register all IPC handlers for the main process.
 *
 * Handlers exposed to the renderer via preload.js contextBridge.
 * All handlers use ipcMain.handle() (async request/response pattern).
 */

"use strict";

const { dialog, shell, app } = require("electron");
const path = require("path");
const log = require("electron-log");

/**
 * Register IPC handlers.
 * @param {Electron.IpcMain} ipcMain
 * @param {{ serverPort: number }} opts
 */
function registerIpcHandlers(ipcMain, { serverPort }) {

  // ── File system dialogs ───────────────────────────────────

  ipcMain.handle("sd:open-file-dialog", async (_event, options = {}) => {
    const { BrowserWindow } = require("electron");
    const win = BrowserWindow.getFocusedWindow();
    return dialog.showOpenDialog(win, {
      properties: ["openFile", "multiSelections"],
      ...options,
    });
  });

  ipcMain.handle("sd:open-folder-dialog", async (_event) => {
    const { BrowserWindow } = require("electron");
    const win = BrowserWindow.getFocusedWindow();
    return dialog.showOpenDialog(win, {
      properties: ["openDirectory"],
    });
  });

  ipcMain.handle("sd:save-file-dialog", async (_event, options = {}) => {
    const { BrowserWindow } = require("electron");
    const win = BrowserWindow.getFocusedWindow();
    return dialog.showSaveDialog(win, options);
  });

  // ── Shell / OS integration ────────────────────────────────

  ipcMain.handle("sd:show-item-in-folder", async (_event, filePath) => {
    if (typeof filePath !== "string" || !filePath) return;
    shell.showItemInFolder(path.normalize(filePath));
  });

  // ── App metadata ──────────────────────────────────────────

  ipcMain.handle("sd:get-app-version", () => app.getVersion());

  ipcMain.handle("sd:get-server-port", () => serverPort);

  ipcMain.handle("sd:get-log-path", () => {
    return log.transports.file.getFile().path;
  });

  // ── Auto-updater ──────────────────────────────────────────

  ipcMain.handle("sd:check-for-updates", async () => {
    if (!app.isPackaged) {
      return { message: "Auto-update disabled in development mode." };
    }
    try {
      const { checkForUpdates } = require("./updater");
      const result = await checkForUpdates();
      return result || { message: "Already on the latest version." };
    } catch (err) {
      return { error: err.message };
    }
  });

  log.info("[ipc-handlers] Registered %d handlers", 7);
}

module.exports = { registerIpcHandlers };
