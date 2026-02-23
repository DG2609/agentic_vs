/**
 * Agentic IDE — Frontend Application
 * File explorer, tabbed code editor, and AI chat panel.
 */

// ── State ──────────────────────────────────────────────────
let ws = null;
let currentThreadId = crypto.randomUUID();
let isStreaming = false;
let currentMsgEl = null;
let contentBuffer = '';
let activeTools = new Map();

// Editor state
let openTabs = [];        // [{path, name, content, original, modified}]
let activeTabPath = null;

// ── DOM ────────────────────────────────────────────────────
const fileTree = document.getElementById('fileTree');
const tabsEl = document.getElementById('tabs');
const editorContent = document.getElementById('editorContent');
const welcomeScreen = document.getElementById('welcomeScreen');
const codeEditor = document.getElementById('codeEditor');
const codeTextarea = document.getElementById('codeTextarea');
const lineNumbers = document.getElementById('lineNumbers');
const fileLang = document.getElementById('fileLang');
const filePathDisplay = document.getElementById('filePathDisplay');
const fileModified = document.getElementById('fileModified');
const saveBtn = document.getElementById('saveBtn');
const editorInfoBar = document.getElementById('editorInfoBar');
const messagesEl = document.getElementById('messages');
const messagesContainer = document.getElementById('messagesContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const chatPanel = document.getElementById('chatPanel');
const toolPanel = document.getElementById('toolPanel');
const statusDot = document.querySelector('.status-dot');

// Panels
const panels = {
    explorer: document.getElementById('explorerPanel'),
    search: document.getElementById('searchPanel'),
    tools: document.getElementById('toolsPanel'),
};

// ── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    configureMarked();
    connectWebSocket();
    loadFileTree('');
    setupEventListeners();
});

function configureMarked() {
    marked.setOptions({
        highlight: (code, lang) => {
            if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
            return hljs.highlightAuto(code).value;
        },
        breaks: true,
        gfm: true,
    });
}

// ── WebSocket ──────────────────────────────────────────────
function connectWebSocket() {
    const p = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${p}//${location.host}/ws/chat`);

    ws.onopen = () => { statusDot.className = 'status-dot connected'; };
    ws.onclose = () => { statusDot.className = 'status-dot'; setTimeout(connectWebSocket, 2000); };
    ws.onerror = () => { statusDot.className = 'status-dot error'; };
    ws.onmessage = (e) => { try { handleChunk(JSON.parse(e.data)); } catch (err) { console.error(err); } };
}

// ── Event Listeners ────────────────────────────────────────
function setupEventListeners() {
    // Chat send
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    messageInput.addEventListener('input', () => {
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + 'px';
    });
    sendBtn.addEventListener('click', sendMessage);

    // Activity bar
    document.querySelectorAll('.activity-btn[data-panel]').forEach(btn => {
        btn.addEventListener('click', () => {
            const panel = btn.dataset.panel;
            if (panel === 'chat') { toggleChat(); return; }
            // Switch sidebar panel
            document.querySelectorAll('.activity-btn').forEach(b => {
                if (b.dataset.panel !== 'chat') b.classList.remove('active');
            });
            btn.classList.add('active');
            Object.entries(panels).forEach(([k, el]) => {
                el.style.display = k === panel ? 'flex' : 'none';
            });
        });
    });

    // Chat toggle
    document.getElementById('chatCloseBtn').addEventListener('click', toggleChat);

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ctrl+S = save
        if (e.ctrlKey && e.key === 's') {
            e.preventDefault();
            saveCurrentFile();
        }
        // Ctrl+Shift+I = toggle chat
        if (e.ctrlKey && e.shiftKey && e.key === 'I') {
            e.preventDefault();
            toggleChat();
        }
    });

    // Code textarea events
    codeTextarea.addEventListener('input', onCodeChange);
    codeTextarea.addEventListener('scroll', syncLineNumbers);
    codeTextarea.addEventListener('keydown', handleTabKey);

    // Save button
    saveBtn.addEventListener('click', saveCurrentFile);

    // Refresh file tree
    document.getElementById('refreshBtn').addEventListener('click', () => loadFileTree(''));

    // Sidebar resize
    setupResize();

    // Search
    document.getElementById('searchInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') performSearch(e.target.value);
    });
}

// ── File Tree ──────────────────────────────────────────────
async function loadFileTree(path, parentEl = null) {
    try {
        const res = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
        const data = await res.json();

        const container = parentEl || fileTree;
        if (!parentEl) container.innerHTML = '';

        if (data.items && data.items.length === 0) {
            container.innerHTML = '<div class="tree-loading">Empty workspace</div>';
            return;
        }

        // Sort: dirs first, then files
        const items = data.items || [];
        items.sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
            return a.name.localeCompare(b.name);
        });

        const depth = path ? path.split('/').length : 0;

        items.forEach(item => {
            const el = document.createElement('div');
            el.className = 'tree-item';
            el.style.setProperty('--depth', depth);

            if (item.is_dir) {
                el.innerHTML = `
                    <span class="tree-arrow">▶</span>
                    <span class="tree-icon">📁</span>
                    <span class="tree-name">${esc(item.name)}</span>
                `;
                let loaded = false;
                let expanded = false;
                const childContainer = document.createElement('div');
                childContainer.style.display = 'none';

                el.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    expanded = !expanded;
                    childContainer.style.display = expanded ? 'block' : 'none';
                    el.querySelector('.tree-arrow').classList.toggle('open', expanded);
                    el.querySelector('.tree-icon').textContent = expanded ? '📂' : '📁';
                    if (!loaded) {
                        loaded = true;
                        await loadFileTree(item.path, childContainer);
                    }
                });

                container.appendChild(el);
                container.appendChild(childContainer);
            } else {
                const icon = getFileIcon(item.name);
                el.innerHTML = `
                    <span class="tree-arrow" style="visibility:hidden">▶</span>
                    <span class="tree-icon">${icon}</span>
                    <span class="tree-name">${esc(item.name)}</span>
                `;
                el.addEventListener('click', () => openFile(item.path, item.name));
                container.appendChild(el);
            }
        });
    } catch (err) {
        const container = parentEl || fileTree;
        container.innerHTML = `<div class="tree-loading" style="color:var(--red)">Error loading files</div>`;
    }
}

function getFileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const icons = {
        py: '🐍', js: '📜', ts: '📘', jsx: '⚛️', tsx: '⚛️',
        html: '🌐', css: '🎨', json: '📋', md: '📝', txt: '📄',
        c: '🔧', cpp: '🔧', h: '🔧', java: '☕', go: '🐹',
        rs: '🦀', m: '📐', slx: '📊', xml: '📰', yml: '⚙️', yaml: '⚙️',
        sh: '🖥️', bat: '🖥️', ps1: '🖥️', toml: '⚙️', cfg: '⚙️',
        gitignore: '🚫', env: '🔒', sql: '🗄️',
    };
    return icons[ext] || '📄';
}

function getLang(name) {
    const ext = name.split('.').pop().toLowerCase();
    const langs = {
        py: 'python', js: 'javascript', ts: 'typescript', jsx: 'jsx', tsx: 'tsx',
        html: 'html', css: 'css', json: 'json', md: 'markdown', c: 'c', cpp: 'cpp',
        h: 'c', java: 'java', go: 'go', rs: 'rust', m: 'matlab', xml: 'xml',
        yml: 'yaml', yaml: 'yaml', sh: 'bash', sql: 'sql', toml: 'toml',
    };
    return langs[ext] || 'plaintext';
}

// ── File Open / Tabs ───────────────────────────────────────
async function openFile(path, name) {
    // Check if already open
    const existing = openTabs.find(t => t.path === path);
    if (existing) {
        switchTab(path);
        return;
    }

    try {
        const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
        const data = await res.json();

        if (data.error) { alert(data.error); return; }

        const tab = {
            path,
            name: name || path.split('/').pop(),
            content: data.content,
            original: data.content,
            modified: false,
        };
        openTabs.push(tab);
        renderTabs();
        switchTab(path);
    } catch (err) {
        console.error('Failed to open file:', err);
    }
}

function switchTab(path) {
    // Save current content
    if (activeTabPath) {
        const cur = openTabs.find(t => t.path === activeTabPath);
        if (cur) cur.content = codeTextarea.value;
    }

    activeTabPath = path;
    const tab = openTabs.find(t => t.path === path);
    if (!tab) return;

    // Show editor, hide welcome
    welcomeScreen.style.display = 'none';
    codeEditor.style.display = 'flex';

    // Load content
    codeTextarea.value = tab.content;
    fileLang.textContent = getLang(tab.name);
    filePathDisplay.textContent = tab.path;
    updateModifiedState(tab);
    updateLineNumbers();
    renderTabs();

    // Highlight active tree item
    document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
}

function closeTab(path, e) {
    if (e) e.stopPropagation();
    const idx = openTabs.findIndex(t => t.path === path);
    if (idx === -1) return;

    const tab = openTabs[idx];
    if (tab.modified && !confirm(`Save changes to ${tab.name}?`)) {
        // discard
    }

    openTabs.splice(idx, 1);

    if (activeTabPath === path) {
        if (openTabs.length > 0) {
            const next = openTabs[Math.max(0, idx - 1)];
            switchTab(next.path);
        } else {
            activeTabPath = null;
            codeEditor.style.display = 'none';
            welcomeScreen.style.display = 'flex';
        }
    }
    renderTabs();
}

function renderTabs() {
    tabsEl.innerHTML = '';
    openTabs.forEach(tab => {
        const el = document.createElement('div');
        el.className = `tab ${tab.path === activeTabPath ? 'active' : ''} ${tab.modified ? 'modified' : ''}`;
        el.innerHTML = `
            <span class="tab-icon">${getFileIcon(tab.name)}</span>
            <span class="tab-name">${esc(tab.name)}</span>
            <button class="tab-close" title="Close">✕</button>
        `;
        el.addEventListener('click', () => switchTab(tab.path));
        el.querySelector('.tab-close').addEventListener('click', (e) => closeTab(tab.path, e));
        tabsEl.appendChild(el);
    });
}

// ── Code Editor ────────────────────────────────────────────
function onCodeChange() {
    const tab = openTabs.find(t => t.path === activeTabPath);
    if (!tab) return;
    tab.content = codeTextarea.value;
    tab.modified = tab.content !== tab.original;
    updateModifiedState(tab);
    updateLineNumbers();
    renderTabs();
}

function updateModifiedState(tab) {
    fileModified.style.display = tab.modified ? 'inline' : 'none';
    saveBtn.style.display = tab.modified ? 'inline-flex' : 'none';
}

function updateLineNumbers() {
    const lines = codeTextarea.value.split('\n').length;
    let nums = '';
    for (let i = 1; i <= lines; i++) nums += i + '\n';
    lineNumbers.textContent = nums;
}

function syncLineNumbers() {
    lineNumbers.scrollTop = codeTextarea.scrollTop;
}

function handleTabKey(e) {
    if (e.key === 'Tab') {
        e.preventDefault();
        const start = codeTextarea.selectionStart;
        const end = codeTextarea.selectionEnd;
        codeTextarea.value = codeTextarea.value.substring(0, start) + '    ' + codeTextarea.value.substring(end);
        codeTextarea.selectionStart = codeTextarea.selectionEnd = start + 4;
        onCodeChange();
    }
}

async function saveCurrentFile() {
    const tab = openTabs.find(t => t.path === activeTabPath);
    if (!tab || !tab.modified) return;

    try {
        const res = await fetch('/api/file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: tab.path, content: tab.content }),
        });
        const data = await res.json();
        if (data.status === 'ok') {
            tab.original = tab.content;
            tab.modified = false;
            updateModifiedState(tab);
            renderTabs();
        }
    } catch (err) {
        console.error('Save failed:', err);
    }
}

// ── Search ─────────────────────────────────────────────────
async function performSearch(query) {
    if (!query.trim()) return;
    const results = document.getElementById('searchResults');
    results.innerHTML = '<div class="tree-loading">Searching...</div>';

    // Use the chat to search (sends through the agent)
    // For now simple client-side via API
    try {
        // We'll use a simple approach: ask the backend
        results.innerHTML = `<div class="search-result-item">Use AI chat to search: "${esc(query)}"</div>`;
    } catch (err) {
        results.innerHTML = `<div class="tree-loading" style="color:var(--red)">Error</div>`;
    }
}

// ── Chat ───────────────────────────────────────────────────
function toggleChat() {
    chatPanel.classList.toggle('hidden');
    const btn = document.getElementById('chatToggleBtn');
    btn.classList.toggle('active');
    document.getElementById('chatBadge').style.display = 'none';
    if (!chatPanel.classList.contains('hidden')) {
        messageInput.focus();
    }
}

function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;

    appendMsg('user', text);
    ws.send(JSON.stringify({ message: text, thread_id: currentThreadId }));

    messageInput.value = '';
    messageInput.style.height = 'auto';
    isStreaming = true;
    sendBtn.disabled = true;
    contentBuffer = '';
    currentMsgEl = appendMsg('assistant', '');
    scrollChat();
}

function handleChunk(chunk) {
    switch (chunk.type) {
        case 'text':
            contentBuffer += chunk.content;
            if (currentMsgEl) {
                currentMsgEl.querySelector('.msg-content').innerHTML = marked.parse(contentBuffer);
                currentMsgEl.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b));
            }
            scrollChat();
            break;

        case 'tool_start':
            if (chunk.tool) {
                addToolUI(chunk.tool);
                if (currentMsgEl) addToolBlock(chunk.tool, 'running');

                // Flash chat badge if panel hidden
                if (chatPanel.classList.contains('hidden')) {
                    document.getElementById('chatBadge').style.display = 'flex';
                }
            }
            break;

        case 'tool_end':
            if (chunk.tool) {
                updateToolUI(chunk.tool);
                if (currentMsgEl) updateToolBlock(chunk.tool);
            }
            break;

        case 'done':
            isStreaming = false;
            sendBtn.disabled = false;
            currentMsgEl = null;
            setTimeout(clearDoneTools, 3000);
            messageInput.focus();
            break;

        case 'error':
            isStreaming = false;
            sendBtn.disabled = false;
            if (currentMsgEl) {
                currentMsgEl.querySelector('.msg-content').innerHTML +=
                    `<p style="color:var(--red)">⚠️ ${esc(chunk.content)}</p>`;
            }
            break;
    }
}

function appendMsg(role, text) {
    const div = document.createElement('div');
    div.className = `message ${role}-message`;
    const content = role === 'user' ? esc(text) :
        (text ? marked.parse(text) : '<div class="typing-dots"><span></span><span></span><span></span></div>');
    div.innerHTML = `<div class="msg-content">${content}</div>`;
    messagesEl.appendChild(div);
    scrollChat();
    return div;
}

function getToolIcon(toolName) {
    const icons = {
        code_search: '🔍', grep_search: '🔍', batch_read: '📚',
        file_read: '📖', file_write: '✏️', file_edit: '✏️',
        file_list: '📂', glob_search: '🔎',
        terminal_exec: '💻', code_analyze: '📊',
        webfetch: '🌐', semantic_search: '🧠', index_codebase: '📇',
        lsp_definition: '🎯', lsp_references: '🔗', lsp_hover: '💡',
        lsp_symbols: '📋', lsp_diagnostics: '🩺',
    };
    return icons[toolName] || '🔧';
}

function addToolBlock(tool, status) {
    const block = document.createElement('div');
    block.className = `tool-block ${status}`;
    block.dataset.toolId = tool.tool_id;

    const args = typeof tool.arguments === 'object'
        ? JSON.stringify(tool.arguments, null, 2) : String(tool.arguments || '');
    const icon = getToolIcon(tool.tool_name);

    block.innerHTML = `
        <div class="tool-block-header ${status}">
            <div class="tool-spinner"></div>
            <span class="tool-block-icon">${icon}</span>
            <span class="tool-block-name">${esc(tool.tool_name)}</span>
            <span class="tool-block-status">Running...</span>
            <span class="tool-block-chevron">▶</span>
        </div>
        <div class="tool-block-body">
            <div class="tool-block-body-inner">
                <div class="tool-block-args">${esc(args)}</div>
                <div class="tool-block-result"></div>
            </div>
        </div>
    `;

    // Toggle expand/collapse
    block.querySelector('.tool-block-header').addEventListener('click', () => {
        const body = block.querySelector('.tool-block-body');
        const chevron = block.querySelector('.tool-block-chevron');
        body.classList.toggle('open');
        chevron.classList.toggle('open');
    });

    // Insert before message content
    const content = currentMsgEl.querySelector('.msg-content');
    currentMsgEl.insertBefore(block, content);
}

function updateToolBlock(tool) {
    if (!currentMsgEl) return;
    const block = currentMsgEl.querySelector(`[data-tool-id="${tool.tool_id}"]`);
    if (!block) return;

    // Update block state
    block.className = `tool-block ${tool.status === 'completed' ? 'completed' : 'error'}`;

    // Update header
    const header = block.querySelector('.tool-block-header');
    header.className = `tool-block-header ${tool.status === 'completed' ? 'completed' : 'error'}`;

    // Replace spinner with check or error icon
    const spinner = header.querySelector('.tool-spinner');
    if (spinner) {
        if (tool.status === 'completed') {
            spinner.outerHTML = '<span class="tool-check">✓</span>';
        } else {
            spinner.outerHTML = '<span class="tool-error-icon">✗</span>';
        }
    }

    // Update status text
    const statusEl = header.querySelector('.tool-block-status');
    if (statusEl) {
        statusEl.textContent = tool.status === 'completed' ? 'Done' : 'Error';
    }

    // Update result
    if (tool.result) {
        const resultEl = block.querySelector('.tool-block-result');
        if (resultEl) resultEl.textContent = tool.result;
    }
}

// ── Tool Panel ─────────────────────────────────────────────
function addToolUI(tool) { activeTools.set(tool.tool_id, tool); renderTools(); }
function updateToolUI(tool) { activeTools.set(tool.tool_id, tool); renderTools(); }
function clearDoneTools() {
    for (const [id, t] of activeTools) { if (t.status === 'completed') activeTools.delete(id); }
    renderTools();
}

function renderTools() {
    if (activeTools.size === 0) {
        toolPanel.innerHTML = '<div class="tool-empty">No tools running</div>';
        return;
    }
    toolPanel.innerHTML = '';
    for (const [, t] of activeTools) {
        const el = document.createElement('div');
        el.className = `tool-item ${t.status}`;
        const icon = t.status === 'running'
            ? '<div class="tool-spinner"></div>'
            : '<span class="tool-check">✓</span>';
        const toolIcon = getToolIcon(t.tool_name);
        el.innerHTML = `${icon}<span>${toolIcon}</span><span class="tool-name">${esc(t.tool_name)}</span>`;
        toolPanel.appendChild(el);
    }
}

// ── Resize Handle ──────────────────────────────────────────
function setupResize() {
    const handle = document.getElementById('sidebarResize');
    const panel = document.getElementById('sidebarPanel');
    let startX, startW;

    handle.addEventListener('mousedown', (e) => {
        startX = e.clientX;
        startW = panel.offsetWidth;
        handle.classList.add('dragging');
        document.addEventListener('mousemove', onDrag);
        document.addEventListener('mouseup', stopDrag);
    });

    function onDrag(e) {
        const w = Math.max(160, Math.min(500, startW + e.clientX - startX));
        panel.style.width = w + 'px';
    }

    function stopDrag() {
        handle.classList.remove('dragging');
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', stopDrag);
    }
}

// ── Utils ──────────────────────────────────────────────────
function scrollChat() {
    requestAnimationFrame(() => { messagesContainer.scrollTop = messagesContainer.scrollHeight; });
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
