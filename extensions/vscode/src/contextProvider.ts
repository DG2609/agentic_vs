/**
 * contextProvider.ts — Collects editor context to augment agent prompts.
 *
 * Provides:
 *   - Active file path (relative to workspace)
 *   - Current selection text
 *   - @file mention resolution (reads file content from workspace)
 *   - Diagnostics (errors/warnings from Problems panel)
 */

import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";

// Match @filename or @path/to/file patterns (not URLs)
const FILE_MENTION_RE = /@([\w./-]+\.\w+)/g;

export interface EditorContext {
  activeFile?: string;
  selection?: string;
  language?: string;
  cursorLine?: number;
  diagnostics?: DiagnosticInfo[];
}

export interface DiagnosticInfo {
  file: string;
  line: number;
  severity: string;
  message: string;
}

/** Collect context from the currently active editor. */
export function getEditorContext(): EditorContext {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return {};
  }

  const doc = editor.document;
  const workspaceRoot =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";

  const activeFile = workspaceRoot
    ? path.relative(workspaceRoot, doc.uri.fsPath)
    : doc.uri.fsPath;

  const selection = editor.selection.isEmpty
    ? undefined
    : doc.getText(editor.selection);

  const cursorLine = editor.selection.active.line + 1;

  return {
    activeFile,
    selection,
    language: doc.languageId,
    cursorLine,
  };
}

/** Get diagnostics (errors/warnings) for the active file. */
export function getActiveDiagnostics(): DiagnosticInfo[] {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return [];
  }

  const diags = vscode.languages.getDiagnostics(editor.document.uri);
  const workspaceRoot =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";

  return diags
    .filter((d) => d.severity <= vscode.DiagnosticSeverity.Warning)
    .slice(0, 20) // cap at 20 to avoid context bloat
    .map((d) => ({
      file: workspaceRoot
        ? path.relative(workspaceRoot, editor.document.uri.fsPath)
        : editor.document.uri.fsPath,
      line: d.range.start.line + 1,
      severity:
        d.severity === vscode.DiagnosticSeverity.Error ? "error" : "warning",
      message: d.message,
    }));
}

/**
 * Resolve @file mentions in a prompt string.
 * Returns the prompt with @file replaced by a fenced code block containing
 * the first 200 lines of that file, or an error note if not found.
 */
export function resolveFileMentions(prompt: string): string {
  const workspaceRoot =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  if (!workspaceRoot) {
    return prompt;
  }

  return prompt.replace(FILE_MENTION_RE, (_match, filePath: string) => {
    const absPath = path.isAbsolute(filePath)
      ? filePath
      : path.join(workspaceRoot, filePath);

    try {
      const content = fs.readFileSync(absPath, "utf-8");
      const lines = content.split("\n").slice(0, 200);
      const truncated = lines.length === 200 ? "\n... (truncated)" : "";
      const ext = path.extname(filePath).slice(1) || "text";
      return `\`${filePath}\`:\n\`\`\`${ext}\n${lines.join("\n")}${truncated}\n\`\`\``;
    } catch {
      return `@${filePath} *(file not found)*`;
    }
  });
}

/**
 * Build a context prefix to prepend to the user's prompt.
 * Includes active file, selection, and optionally diagnostics.
 */
export function buildContextPrefix(
  ctx: EditorContext,
  includeDiagnostics = false
): string {
  const parts: string[] = [];

  if (ctx.activeFile) {
    let fileRef = `**Active file:** \`${ctx.activeFile}\``;
    if (ctx.language) {
      fileRef += ` (${ctx.language})`;
    }
    if (ctx.cursorLine) {
      fileRef += ` — cursor at line ${ctx.cursorLine}`;
    }
    parts.push(fileRef);
  }

  if (ctx.selection) {
    parts.push(
      `**Selected text:**\n\`\`\`${ctx.language ?? ""}\n${ctx.selection}\n\`\`\``
    );
  }

  if (includeDiagnostics) {
    const diags = getActiveDiagnostics();
    if (diags.length > 0) {
      const diagLines = diags.map(
        (d) => `  - [${d.severity}] ${d.file}:${d.line} — ${d.message}`
      );
      parts.push(`**Diagnostics:**\n${diagLines.join("\n")}`);
    }
  }

  return parts.length > 0 ? `${parts.join("\n\n")}\n\n---\n\n` : "";
}

/**
 * Open VS Code's built-in diff editor for a modified file.
 * Shows `original` vs `modified` content side by side.
 */
export async function showFileDiff(
  filePath: string,
  modifiedContent: string
): Promise<void> {
  const workspaceRoot =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  const absPath = path.isAbsolute(filePath)
    ? filePath
    : path.join(workspaceRoot, filePath);

  const originalUri = vscode.Uri.file(absPath);

  // Write modified content to a temp file via untitled URI
  const modifiedUri = vscode.Uri.parse(
    `untitled:${absPath}.shadowdev-modified`
  );

  const doc = await vscode.workspace.openTextDocument(modifiedUri);
  const editor = await vscode.window.showTextDocument(doc, { preview: true });
  await editor.edit((eb) => {
    const fullRange = new vscode.Range(
      doc.positionAt(0),
      doc.positionAt(doc.getText().length)
    );
    eb.replace(fullRange, modifiedContent);
  });

  await vscode.commands.executeCommand(
    "vscode.diff",
    originalUri,
    modifiedUri,
    `ShadowDev: ${path.basename(filePath)}`
  );
}
