/**
 * extension.ts — Main entry point for the ShadowDev VS Code extension.
 *
 * Registers:
 *   - ChatViewProvider (sidebar webview)
 *   - shadowdev.openChat
 *   - shadowdev.sendSelection
 *   - shadowdev.explainFile
 *   - shadowdev.reviewFile
 *   - shadowdev.runInTerminal
 *   - shadowdev.newSession
 */

import * as vscode from "vscode";
import { ChatViewProvider } from "./chatPanel";
import {
  getEditorContext,
  buildContextPrefix,
  resolveFileMentions,
} from "./contextProvider";

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ChatViewProvider(context.extensionUri);

  // Register the sidebar WebviewViewProvider
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      ChatViewProvider.viewType,
      provider,
      { webviewOptions: { retainContextWhenHidden: true } }
    )
  );

  // ── shadowdev.openChat ───────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.openChat", () => {
      vscode.commands.executeCommand("shadowdev.chatView.focus");
    })
  );

  // ── shadowdev.newSession ─────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.newSession", () => {
      provider.newSession();
      vscode.commands.executeCommand("shadowdev.chatView.focus");
    })
  );

  // ── shadowdev.sendSelection ──────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.sendSelection", async () => {
      const ctx = getEditorContext();
      if (!ctx.selection) {
        vscode.window.showInformationMessage(
          "ShadowDev: No text selected. Select some code first."
        );
        return;
      }

      const action = await vscode.window.showQuickPick(
        [
          { label: "$(comment) Ask about selection", value: "ask" },
          { label: "$(symbol-method) Explain this code", value: "explain" },
          { label: "$(bug) Find bugs in this code", value: "bugs" },
          { label: "$(edit) Refactor this code", value: "refactor" },
          { label: "$(beaker) Write tests for this", value: "tests" },
        ],
        { title: "ShadowDev — What do you want to do with the selection?" }
      );

      if (!action) {
        return;
      }

      const prefix = buildContextPrefix(ctx);
      let prompt: string;

      switch (action.value) {
        case "explain":
          prompt = `${prefix}Explain the following code in detail:\n\`\`\`${ctx.language ?? ""}\n${ctx.selection}\n\`\`\``;
          break;
        case "bugs":
          prompt = `${prefix}Find and explain any bugs or issues in:\n\`\`\`${ctx.language ?? ""}\n${ctx.selection}\n\`\`\``;
          break;
        case "refactor":
          prompt = `${prefix}Refactor this code for clarity and best practices:\n\`\`\`${ctx.language ?? ""}\n${ctx.selection}\n\`\`\``;
          break;
        case "tests":
          prompt = `${prefix}Write comprehensive unit tests for:\n\`\`\`${ctx.language ?? ""}\n${ctx.selection}\n\`\`\``;
          break;
        default:
          prompt = `${prefix}${ctx.selection}`;
      }

      await provider.sendPrompt(prompt);
    })
  );

  // ── shadowdev.explainFile ────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.explainFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showInformationMessage("ShadowDev: No active file.");
        return;
      }

      const ctx = getEditorContext();
      const fileName = ctx.activeFile ?? editor.document.fileName;
      const prefix = buildContextPrefix({ ...ctx, selection: undefined });

      await provider.sendPrompt(
        `${prefix}Explain the purpose and architecture of \`${fileName}\`. Include the main functions/classes and how they interact.`
      );
    })
  );

  // ── shadowdev.reviewFile ─────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.reviewFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showInformationMessage("ShadowDev: No active file.");
        return;
      }

      const ctx = getEditorContext();
      const fileName = ctx.activeFile ?? editor.document.fileName;
      const prefix = buildContextPrefix({ ...ctx, selection: undefined }, true);

      await provider.sendPrompt(
        `${prefix}Perform a thorough code review of \`${fileName}\`. Check for: bugs, security issues, performance problems, code quality, missing error handling, and improvements. Provide a prioritized list of findings.`
      );
    })
  );

  // ── shadowdev.runInTerminal ──────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("shadowdev.runInTerminal", async () => {
      const cfg = vscode.workspace.getConfiguration("shadowdev");
      const pythonPath = cfg.get<string>("pythonPath", "python");
      const workspacePath =
        cfg.get<string>("workspacePath", "") ||
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ||
        "";

      const prompt = await vscode.window.showInputBox({
        title: "ShadowDev — Run in Terminal",
        prompt: "Enter your prompt for the agent",
        placeHolder: "e.g. Fix the failing tests in tests/",
      });

      if (!prompt) {
        return;
      }

      const terminal =
        vscode.window.terminals.find((t) => t.name === "ShadowDev") ??
        vscode.window.createTerminal({
          name: "ShadowDev",
          cwd: workspacePath,
        });

      terminal.show();

      // Escape prompt for shell safety
      const escapedPrompt = prompt.replace(/'/g, "'\\''");
      terminal.sendText(
        `${pythonPath} cli.py --prompt '${escapedPrompt}' --output-format text`,
        true
      );
    })
  );

  // ── Status bar item ──────────────────────────────────────
  const statusBar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBar.command = "shadowdev.openChat";
  statusBar.text = "$(robot) ShadowDev";
  statusBar.tooltip = "Open ShadowDev Chat (Ctrl+Shift+D)";
  statusBar.show();
  context.subscriptions.push(statusBar);

  // ── Diagnostic watcher (Problems panel) ─────────────────
  // Show a notification when new errors appear and offer to ask ShadowDev
  let lastDiagCount = 0;
  context.subscriptions.push(
    vscode.languages.onDidChangeDiagnostics((e) => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || !e.uris.some((u) => u.toString() === editor.document.uri.toString())) {
        return;
      }

      const errors = vscode.languages
        .getDiagnostics(editor.document.uri)
        .filter((d) => d.severity === vscode.DiagnosticSeverity.Error);

      // Offer help only when new errors appear (not on clear)
      if (errors.length > 0 && errors.length > lastDiagCount) {
        lastDiagCount = errors.length;
        vscode.window
          .showInformationMessage(
            `ShadowDev: ${errors.length} error(s) in ${editor.document.fileName.split("/").pop()}. Ask for help?`,
            "Fix with ShadowDev",
            "Dismiss"
          )
          .then((choice) => {
            if (choice === "Fix with ShadowDev") {
              const ctx = getEditorContext();
              const prefix = buildContextPrefix(ctx, true);
              provider.sendPrompt(
                `${prefix}There are ${errors.length} error(s) in this file. Please diagnose and fix them.`
              );
              vscode.commands.executeCommand("shadowdev.chatView.focus");
            }
          });
      } else if (errors.length === 0) {
        lastDiagCount = 0;
      }
    })
  );
}

export function deactivate(): void {
  // Nothing to clean up — subscriptions are disposed automatically
}
