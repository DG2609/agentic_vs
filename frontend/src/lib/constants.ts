import type { ToolBlock as ToolBlockType } from './types';

// ── Tool Icons ──────────────────────────────────────────────
export const TOOL_ICONS: Record<string, string> = {
    code_search: '🔍', grep_search: '🔍', batch_read: '📚',
    file_read: '📖', file_write: '✏️', file_edit: '✏️', multi_edit: '✏️',
    file_list: '📂', glob_search: '🔎',
    terminal_exec: '💻', code_analyze: '📊',
    webfetch: '🌐', web_search: '🔎',
    semantic_search: '🧠', index_codebase: '📇',
    lsp_definition: '🎯', lsp_references: '🔗', lsp_hover: '💡',
    lsp_symbols: '📋', lsp_diagnostics: '🩺',
    todo_read: '📝', todo_write: '📝',
    question: '❓', plan_enter: '📐', plan_exit: '📐',
    task_explore: '🔭', task_general: '🤖',
};

// ── File Icons ──────────────────────────────────────────────
export function getFileIcon(name: string): string {
    const ext = name.split('.').pop()?.toLowerCase() || '';
    const icons: Record<string, string> = {
        py: '🐍', js: '📜', ts: '💠', tsx: '⚛️', jsx: '⚛️',
        json: '📋', md: '📝', html: '🌐', css: '🎨',
        txt: '📄', yml: '⚙️', yaml: '⚙️', toml: '⚙️',
        sh: '🖥️', sql: '🗃️', rs: '🦀', go: '🐹',
    };
    return icons[ext] || '📄';
}

export function getLang(name: string): string {
    const ext = name.split('.').pop()?.toLowerCase() || '';
    const langs: Record<string, string> = {
        py: 'python', js: 'javascript', ts: 'typescript', tsx: 'tsx',
        json: 'json', md: 'markdown', html: 'html', css: 'css',
        sh: 'bash', sql: 'sql', rs: 'rust', go: 'go',
    };
    return langs[ext] || 'plaintext';
}
