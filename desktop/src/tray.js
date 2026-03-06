/**
 * tray.js — System tray icon and context menu for ShadowDev.
 *
 * Behaviour:
 *   - Click tray icon   → show/focus main window
 *   - Right-click menu  → Open, New Session, Separator, Quit
 *   - macOS: menubar icon (no taskbar)
 *   - Win/Linux: taskbar icon, hides to tray on close
 */

"use strict";

const { Tray, Menu, nativeImage } = require("electron");
const path = require("path");
const log = require("electron-log");

/** @type {Tray | null} */
let tray = null;

/**
 * Create and set up the system tray icon.
 *
 * @param {{ showWindow: () => void, app: Electron.App }} opts
 */
function setupTray({ showWindow, app }) {
  const iconPath = path.join(__dirname, "..", "assets", "tray-icon.png");

  try {
    const icon = nativeImage.createFromPath(iconPath);
    if (icon.isEmpty()) {
      // Fallback: create a minimal 16x16 placeholder if the asset is missing
      log.warn("[tray] tray-icon.png not found, using empty placeholder");
      tray = new Tray(nativeImage.createEmpty());
    } else {
      // Resize to standard tray sizes (16px Win/Linux, 22px macOS retina)
      tray = new Tray(icon.resize({ width: 16, height: 16 }));
    }
  } catch (err) {
    log.error("[tray] Failed to create tray icon:", err.message);
    return;
  }

  tray.setToolTip("ShadowDev — Agentic IDE");

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "Open ShadowDev",
      click: showWindow,
    },
    {
      label: "New Session",
      click: () => {
        showWindow();
        // The renderer listens for this event to clear chat history
        const { BrowserWindow } = require("electron");
        const win = BrowserWindow.getAllWindows()[0];
        if (win) {
          win.webContents.send("sd:new-session");
        }
      },
    },
    { type: "separator" },
    {
      label: "Check for Updates…",
      click: () => {
        const { BrowserWindow } = require("electron");
        const win = BrowserWindow.getAllWindows()[0];
        if (win) {
          win.webContents.send("sd:check-for-updates-trigger");
        }
      },
    },
    { type: "separator" },
    {
      label: "Quit ShadowDev",
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);

  // Single-click on Windows/Linux shows the window
  tray.on("click", () => {
    showWindow();
  });

  log.info("[tray] System tray initialized");
  return tray;
}

function destroyTray() {
  if (tray && !tray.isDestroyed()) {
    tray.destroy();
    tray = null;
  }
}

module.exports = { setupTray, destroyTray };
