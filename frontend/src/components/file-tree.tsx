'use client';

import { useState, useEffect, useCallback } from 'react';

import { FiChevronRight, FiRefreshCw, FiSearch, FiFolderPlus } from 'react-icons/fi';
import { getFileIcon } from '@/lib/constants';
import type { FileItem, ToolBlock as ToolBlockType } from '@/lib/types';
import { TOOL_ICONS } from '@/lib/constants';

// ── File Tree ───────────────────────────────────────────────

interface FileTreeProps {
    fileTree: FileItem[];
    expandedDirs: Record<string, FileItem[]>;
    activeTab: string | null;
    onToggleDir: (path: string) => void;
    onOpenFile: (path: string, name: string) => void;
    onRefresh: () => void;
    onOpenFolder: () => void;
}

export default function FileTree({ fileTree, expandedDirs, activeTab, onToggleDir, onOpenFile, onRefresh, onOpenFolder }: FileTreeProps) {
    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center justify-between px-4 py-2.5 flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
                <span className="text-[11px] font-semibold tracking-wider text-[var(--text-secondary)]">EXPLORER</span>
                <div className="flex items-center gap-1">
                    <button onClick={onOpenFolder} title="Open Folder"
                        className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors p-1 rounded">
                        <FiFolderPlus size={14} />
                    </button>
                    <button onClick={onRefresh} title="Refresh"
                        className="text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:rotate-90 transition-all p-1 rounded">
                        <FiRefreshCw size={14} />
                    </button>
                </div>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
                <FileTreeView items={fileTree} depth={0} expandedDirs={expandedDirs}
                    toggleDir={onToggleDir} openFile={onOpenFile} activeTab={activeTab} />
            </div>
        </div>
    );
}

// ── Recursive File Tree View ────────────────────────────────

function FileTreeView({
    items, depth, expandedDirs, toggleDir, openFile, activeTab,
}: {
    items: FileItem[];
    depth: number;
    expandedDirs: Record<string, FileItem[]>;
    toggleDir: (path: string) => void;
    openFile: (path: string, name: string) => void;
    activeTab: string | null;
}) {
    return (
        <>
            {items.map((item) => (
                <div key={item.path}>
                    <div
                        className={`flex items-center gap-1.5 py-0.5 pr-2 cursor-pointer transition-all text-[13px] select-none
              ${activeTab === item.path ? 'text-[var(--accent-bright)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]'}`}
                        style={{
                            paddingLeft: `${depth * 16 + 8}px`,
                            borderLeft: '2px solid transparent',
                            ...(activeTab === item.path ? { background: 'var(--accent-soft)', borderLeftColor: 'var(--accent)' } : {}),
                        }}
                        onClick={() => item.is_dir ? toggleDir(item.path) : openFile(item.path, item.name)}>
                        {item.is_dir ? (
                            <FiChevronRight size={12}
                                className={`transition-transform flex-shrink-0 ${expandedDirs[item.path] ? 'rotate-90' : ''}`}
                                style={{ color: 'var(--text-muted)' }} />
                        ) : (
                            <span className="w-3 flex-shrink-0" />
                        )}
                        <span className="text-[14px] flex-shrink-0">
                            {item.is_dir ? '📁' : getFileIcon(item.name)}
                        </span>
                        <span className="overflow-hidden text-ellipsis whitespace-nowrap">{item.name}</span>
                    </div>
                    {item.is_dir && expandedDirs[item.path] && (
                        <FileTreeView items={expandedDirs[item.path]} depth={depth + 1}
                            expandedDirs={expandedDirs} toggleDir={toggleDir}
                            openFile={openFile} activeTab={activeTab} />
                    )}
                </div>
            ))}
        </>
    );
}

// ── Search Panel ────────────────────────────────────────────

interface SearchPanelProps {
    query: string;
    onQueryChange: (q: string) => void;
    onSearch: () => void;
    results: Array<{ file: string; line: number; content: string }>;
    onOpenFile: (path: string, name: string) => void;
}

export function SearchPanel({ query, onQueryChange, onSearch, results, onOpenFile }: SearchPanelProps) {
    return (
        <div className="flex flex-col h-full">
            <div className="px-4 py-2.5 flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
                <span className="text-[11px] font-semibold tracking-wider text-[var(--text-secondary)]">SEARCH</span>
            </div>
            <div className="px-3 py-2" style={{ borderBottom: '1px solid var(--border)' }}>
                <div className="flex items-center gap-1.5 rounded-md transition-all"
                    style={{ background: 'var(--bg-input)', border: '1px solid var(--border)' }}>
                    <FiSearch size={13} className="ml-2.5 flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
                    <input type="text" value={query}
                        onChange={(e) => onQueryChange(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && onSearch()}
                        placeholder="Search in files..."
                        className="w-full px-1.5 py-1.5 text-[13px] outline-none bg-transparent"
                        style={{ color: 'var(--text-primary)' }} />
                </div>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
                {results.length === 0 && query && (
                    <p className="text-[12px] text-[var(--text-muted)] italic px-3 py-2">No results</p>
                )}
                {results.map((r, i) => (
                    <div key={i} className="px-3 py-1.5 text-[12px] cursor-pointer hover:bg-[var(--bg-hover)] transition-colors"
                        onClick={() => onOpenFile(r.file, r.file.split('/').pop()!)}>
                        <span style={{ color: 'var(--blue)' }}>{r.file}</span>
                        <span style={{ color: 'var(--text-muted)' }}>:{r.line}</span>
                        <div className="mt-0.5 truncate" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                            {r.content}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Active Tools Panel ──────────────────────────────────────

interface ToolsPanelProps {
    activeTools: Map<string, ToolBlockType>;
}

export function ToolsPanel({ activeTools }: ToolsPanelProps) {
    return (
        <div className="flex flex-col h-full">
            <div className="px-4 py-2.5 flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
                <span className="text-[11px] font-semibold tracking-wider text-[var(--text-secondary)]">ACTIVE TOOLS</span>
                {activeTools.size > 0 && (
                    <span className="ml-2 px-1.5 py-0.5 text-[10px] rounded-full"
                        style={{ background: 'var(--accent-soft)', color: 'var(--accent-bright)' }}>
                        {activeTools.size}
                    </span>
                )}
            </div>
            <div className="flex-1 overflow-y-auto px-3 py-2">
                {activeTools.size === 0 ? (
                    <p className="text-[var(--text-muted)] text-[12px] italic">No tools running</p>
                ) : (
                    Array.from(activeTools.values()).map((t) => (
                        <div key={t.tool_id}
                            className="flex items-center gap-2 px-2.5 py-1.5 mb-1 rounded-md text-[12px]"
                            style={{
                                background: 'var(--bg-input)',
                                border: `1px solid ${t.status === 'running' ? 'var(--accent)' : 'var(--border)'}`,
                                animation: 'slideIn 0.2s ease',
                                opacity: t.status === 'completed' ? 0.6 : 1,
                            }}>
                            {t.status === 'running' ? (
                                <div className="w-3.5 h-3.5 rounded-full border-2 flex-shrink-0"
                                    style={{ borderColor: 'rgba(124,108,240,0.2)', borderTopColor: 'var(--accent-bright)', animation: 'spin 0.7s linear infinite' }} />
                            ) : (
                                <span style={{ color: 'var(--green)' }}>✓</span>
                            )}
                            <span>{TOOL_ICONS[t.tool_name] || '🔧'}</span>
                            <span style={{ color: 'var(--text-secondary)' }}>{t.tool_name}</span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

// ── Git Panel ───────────────────────────────────────────────

interface GitFile { status: string; path: string; }

export function GitPanel() {
    const [files, setFiles] = useState<GitFile[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const loadStatus = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const hostname = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
            const res = await fetch(`http://${hostname}:8000/api/git/status`);
            const data = await res.json();
            if (data.error) setError(data.error);
            else setFiles(data.files || []);
        } catch (e: any) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadStatus(); }, [loadStatus]);

    return (
        <div className="flex flex-col h-full">
            <div className="px-4 py-2.5 flex items-center justify-between flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
                <span className="text-[11px] font-semibold tracking-wider text-[var(--text-secondary)]">SOURCE CONTROL</span>
                <button onClick={loadStatus} disabled={loading} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors">
                    <FiRefreshCw size={12} className={loading ? 'animate-spin' : ''} />
                </button>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
                {error ? (
                    <div className="px-4 py-3 text-[12px] text-[var(--red)]">{error}</div>
                ) : files.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-40 gap-3 opacity-60 text-center">
                        <span className="text-[24px]">🐙</span>
                        <span className="text-[var(--text-muted)] text-[12px]">Working tree clean</span>
                    </div>
                ) : (
                    <div className="flex flex-col">
                        {files.map((f, i) => (
                            <div key={i} className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] hover:bg-[var(--bg-hover)] cursor-default transition-colors group">
                                <span className="font-mono text-[11px] font-medium w-4 text-center"
                                    style={{ color: f.status.includes('M') ? 'var(--blue)' : f.status.includes('?') ? 'var(--green)' : f.status.includes('D') ? 'var(--red)' : 'var(--text-secondary)' }}>
                                    {f.status.trim() || 'M'}
                                </span>
                                <span className="truncate flex-1" style={{ color: 'var(--text-primary)' }}>{f.path.split('/').pop()}</span>
                                <span className="text-[10px] truncate opacity-0 group-hover:opacity-60 transition-opacity" style={{ color: 'var(--text-muted)' }}>
                                    {f.path}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
