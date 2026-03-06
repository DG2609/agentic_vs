/**
 * chatPanel.ts — WebviewViewProvider for the ShadowDev chat sidebar.
 *
 * Features:
 *   - Message history (user + assistant bubbles)
 *   - @file mention autocomplete hint
 *   - Active file / selection context injection
 *   - Streaming response support
 *   - Dark/light theme aware
 *   - "Copy" button on assistant messages
 */

import * as vscode from "vscode";
import * as crypto from "crypto";
import {
  getEditorContext,
  buildContextPrefix,
  resolveFileMentions,
} from "./contextProvider";
import { runAgent } from "./shadowdevClient";

export class ChatViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "shadowdev.chatView";
  private _view?: vscode.WebviewView;
  private _sessionId: string;

  constructor(private readonly _extensionUri: vscode.Uri) {
    this._sessionId = this._newSessionId();
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };
    webviewView.webview.html = this._getHtml(webviewView.webview);

    // Handle messages from the webview
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case "send":
          await this._handleUserMessage(msg.text);
          break;
        case "new_session":
          this._newSession();
          break;
        case "copy":
          vscode.env.clipboard.writeText(msg.text);
          break;
      }
    });
  }

  /** Send a pre-built prompt (e.g. from editor context menu). */
  public async sendPrompt(prompt: string): Promise<void> {
    await this._show();
    await this._handleUserMessage(prompt);
  }

  /** Start a new session (clears history). */
  public newSession(): void {
    this._newSession();
  }

  // ── Private ────────────────────────────────────────────────

  private _newSessionId(): string {
    return `vscode-${crypto.randomBytes(6).toString("hex")}`;
  }

  private _newSession(): void {
    this._sessionId = this._newSessionId();
    this._post({ type: "clear" });
  }

  private async _show(): Promise<void> {
    await vscode.commands.executeCommand("shadowdev.chatView.focus");
  }

  private async _handleUserMessage(userText: string): Promise<void> {
    if (!userText.trim()) {
      return;
    }

    const cfg = vscode.workspace.getConfiguration("shadowdev");
    const includeFile = cfg.get<boolean>("includeActiveFile", true);

    // Build context prefix
    const editorCtx = getEditorContext();
    const contextPrefix = includeFile
      ? buildContextPrefix(editorCtx, true)
      : "";

    // Resolve @file mentions
    const resolvedText = resolveFileMentions(userText);
    const fullPrompt = contextPrefix + resolvedText;

    // Show user message (original text, not with resolved files)
    this._post({ type: "user", text: userText });
    this._post({ type: "thinking" });

    let accumulated = "";
    let sessionIdFromResponse: string | undefined;

    try {
      const response = await runAgent(
        {
          prompt: fullPrompt,
          sessionId: this._sessionId,
        },
        (chunk) => {
          // Streaming chunk
          accumulated += chunk;
          this._post({ type: "stream_chunk", text: chunk });
        }
      );

      // Non-streaming or final response
      const finalText = response.response || accumulated;
      sessionIdFromResponse = response.session_id;

      if (response.error) {
        this._post({ type: "error", text: response.error });
      } else {
        this._post({ type: "assistant", text: finalText });
      }

      // Update session ID if server returned one
      if (sessionIdFromResponse) {
        this._sessionId = sessionIdFromResponse;
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      this._post({ type: "error", text: `Unexpected error: ${msg}` });
    }
  }

  private _post(msg: Record<string, unknown>): void {
    this._view?.webview.postMessage(msg);
  }

  private _getHtml(webview: vscode.Webview): string {
    const nonce = crypto.randomBytes(16).toString("hex");

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ShadowDev</title>
  <style nonce="${nonce}">
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    #messages {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .msg {
      border-radius: 6px;
      padding: 8px 10px;
      max-width: 100%;
      word-wrap: break-word;
      white-space: pre-wrap;
      font-size: 12px;
      line-height: 1.5;
      position: relative;
    }

    .msg.user {
      background: var(--vscode-inputOption-activeBackground);
      border: 1px solid var(--vscode-inputOption-activeBorder, transparent);
      align-self: flex-end;
      max-width: 88%;
    }

    .msg.assistant {
      background: var(--vscode-editor-inactiveSelectionBackground);
      border: 1px solid var(--vscode-panel-border, transparent);
      align-self: flex-start;
      max-width: 100%;
    }

    .msg.error {
      background: var(--vscode-inputValidation-errorBackground);
      border: 1px solid var(--vscode-inputValidation-errorBorder);
      color: var(--vscode-inputValidation-errorForeground);
    }

    .msg.thinking {
      color: var(--vscode-descriptionForeground);
      font-style: italic;
      font-size: 11px;
      padding: 4px 8px;
      background: transparent;
    }

    .msg-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 3px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .copy-btn {
      position: absolute;
      top: 4px;
      right: 4px;
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      border: none;
      border-radius: 3px;
      padding: 1px 5px;
      font-size: 10px;
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.15s;
    }
    .msg.assistant:hover .copy-btn { opacity: 1; }
    .copy-btn:hover { background: var(--vscode-button-hoverBackground); }

    code {
      background: var(--vscode-textCodeBlock-background);
      padding: 0 3px;
      border-radius: 3px;
      font-family: var(--vscode-editor-font-family);
    }

    pre {
      background: var(--vscode-textCodeBlock-background);
      border-radius: 4px;
      padding: 8px;
      overflow-x: auto;
      font-family: var(--vscode-editor-font-family);
      font-size: 11px;
      line-height: 1.4;
      margin: 4px 0;
    }

    pre code { background: none; padding: 0; }

    #input-area {
      border-top: 1px solid var(--vscode-panel-border);
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    #input-hint {
      font-size: 10px;
      color: var(--vscode-descriptionForeground);
      padding: 0 2px;
    }

    #input-row {
      display: flex;
      gap: 4px;
    }

    #prompt {
      flex: 1;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border);
      border-radius: 4px;
      padding: 6px 8px;
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      resize: vertical;
      min-height: 56px;
      max-height: 200px;
      outline: none;
    }
    #prompt:focus {
      border-color: var(--vscode-focusBorder);
    }

    #send-btn {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 4px;
      padding: 6px 10px;
      cursor: pointer;
      font-size: 12px;
      align-self: flex-end;
      white-space: nowrap;
    }
    #send-btn:hover { background: var(--vscode-button-hoverBackground); }
    #send-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .badge {
      display: inline-block;
      background: var(--vscode-badge-background);
      color: var(--vscode-badge-foreground);
      border-radius: 10px;
      padding: 1px 6px;
      font-size: 10px;
      font-weight: 600;
      margin-right: 4px;
    }
  </style>
</head>
<body>
  <div id="messages"></div>

  <div id="input-area">
    <div id="input-hint">Use @filename.py to include a file. Ctrl+Enter to send.</div>
    <div id="input-row">
      <textarea id="prompt" rows="3" placeholder="Ask ShadowDev anything…"></textarea>
      <button id="send-btn">Send</button>
    </div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const promptEl   = document.getElementById('prompt');
    const sendBtn    = document.getElementById('send-btn');

    let streaming    = false;
    let streamMsgEl  = null;
    let thinking     = null;

    function scrollBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function escapeHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function renderMarkdown(text) {
      // Minimal markdown: code blocks, inline code, bold, newlines
      return text
        .replace(/\`\`\`(\w*)\n?([\s\S]*?)\`\`\`/g, (_, lang, code) =>
          \`<pre><code class="\${escapeHtml(lang)}">\${escapeHtml(code.trim())}</code></pre>\`)
        .replace(/\`([^\`]+)\`/g, (_, c) => \`<code>\${escapeHtml(c)}</code>\`)
        .replace(/\*\*([^*]+)\*\*/g, (_, b) => \`<strong>\${escapeHtml(b)}</strong>\`)
        .replace(/\n/g, '<br>');
    }

    function addMessage(type, text) {
      if (thinking && type !== 'stream_chunk') {
        thinking.remove();
        thinking = null;
      }

      if (type === 'thinking') {
        const el = document.createElement('div');
        el.className = 'msg thinking';
        el.textContent = '⏳ Thinking…';
        messagesEl.appendChild(el);
        thinking = el;
        scrollBottom();
        return;
      }

      if (type === 'stream_chunk') {
        if (thinking) { thinking.remove(); thinking = null; }
        if (!streamMsgEl) {
          streamMsgEl = createMsgEl('assistant', '');
          messagesEl.appendChild(streamMsgEl);
        }
        streamMsgEl.querySelector('.msg-body').innerHTML += escapeHtml(text);
        scrollBottom();
        return;
      }

      // Finalize stream
      if (type === 'assistant' && streamMsgEl) {
        // Replace streamed content with rendered markdown
        streamMsgEl.querySelector('.msg-body').innerHTML = renderMarkdown(text);
        streamMsgEl = null;
        scrollBottom();
        return;
      }

      const el = createMsgEl(type, text);
      messagesEl.appendChild(el);
      scrollBottom();
    }

    function createMsgEl(type, text) {
      const el = document.createElement('div');
      el.className = 'msg ' + type;

      const label = document.createElement('div');
      label.className = 'msg-label';
      label.textContent = type === 'user' ? 'You' : type === 'error' ? '⚠ Error' : '🤖 ShadowDev';
      el.appendChild(label);

      const body = document.createElement('div');
      body.className = 'msg-body';
      body.innerHTML = type === 'user' ? escapeHtml(text) : renderMarkdown(text);
      el.appendChild(body);

      if (type === 'assistant') {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.textContent = 'Copy';
        copyBtn.onclick = () => vscode.postMessage({ type: 'copy', text });
        el.appendChild(copyBtn);
      }

      return el;
    }

    function setLoading(loading) {
      sendBtn.disabled = loading;
      promptEl.disabled = loading;
      sendBtn.textContent = loading ? '…' : 'Send';
    }

    async function sendMessage() {
      const text = promptEl.value.trim();
      if (!text || sendBtn.disabled) return;

      promptEl.value = '';
      promptEl.style.height = '';
      setLoading(true);
      streamMsgEl = null;

      vscode.postMessage({ type: 'send', text });
    }

    sendBtn.addEventListener('click', sendMessage);

    promptEl.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        sendMessage();
      }
    });

    // Auto-resize textarea
    promptEl.addEventListener('input', () => {
      promptEl.style.height = 'auto';
      promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + 'px';
    });

    // Handle messages from extension host
    window.addEventListener('message', (event) => {
      const msg = event.data;
      switch (msg.type) {
        case 'user':
          addMessage('user', msg.text);
          break;
        case 'assistant':
          setLoading(false);
          addMessage('assistant', msg.text);
          break;
        case 'error':
          setLoading(false);
          addMessage('error', msg.text);
          break;
        case 'thinking':
          addMessage('thinking', '');
          break;
        case 'stream_chunk':
          addMessage('stream_chunk', msg.text);
          break;
        case 'clear':
          messagesEl.innerHTML = '';
          setLoading(false);
          break;
      }
    });
  </script>
</body>
</html>`;
  }
}
