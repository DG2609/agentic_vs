'use client';

import { FiX, FiSave, FiCheck, FiCornerUpLeft } from 'react-icons/fi';
import { getFileIcon, getLang } from '@/lib/constants';
import Editor, { DiffEditor } from '@monaco-editor/react';
import type { OpenTab, ModelInfo } from '@/lib/types';

interface CodeEditorProps {
    tabs: OpenTab[];
    activeTab: string | null;
    onSelectTab: (path: string) => void;
    onCloseTab: (path: string, e?: React.MouseEvent) => void;
    onContentChange: (path: string, content: string) => void;
    onSave: () => void;
    modelInfo: ModelInfo | null;
    onNewConversation: () => void;
    onOpenChat: () => void;
    onApproveDiff: (path: string) => void;
    onRejectDiff: (path: string) => void;
}

export default function CodeEditor({
    tabs, activeTab, onSelectTab, onCloseTab, onContentChange, onSave,
    modelInfo, onNewConversation, onOpenChat, onApproveDiff, onRejectDiff
}: CodeEditorProps) {
    const currentTab = tabs.find((t) => t.path === activeTab);

    // Map file extensions to Monaco languages
    const getLanguage = (filename: string) => {
        const ext = filename.split('.').pop()?.toLowerCase();
        const map: Record<string, string> = {
            'js': 'javascript', 'jsx': 'javascript',
            'ts': 'typescript', 'tsx': 'typescript',
            'py': 'python', 'json': 'json', 'md': 'markdown',
            'html': 'html', 'css': 'css', 'yaml': 'yaml', 'yml': 'yaml'
        };
        return map[ext || ''] || 'plaintext';
    };

    return (
        <div className="flex-1 flex flex-col min-w-0 backdrop-blur-3xl" style={{ background: 'var(--bg-editor)' }}>
            {/* Tab Bar */}
            <div className="h-9 flex items-stretch overflow-x-auto flex-shrink-0"
                style={{ background: 'var(--bg-dark)', borderBottom: '1px solid var(--border)' }}>
                {tabs.map((tab) => (
                    <div key={tab.path}
                        className={`flex items-center gap-1.5 px-3.5 text-[12px] cursor-pointer whitespace-nowrap transition-all relative group
              ${activeTab === tab.path ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}
                        style={{
                            background: activeTab === tab.path ? 'var(--bg-editor)' : 'transparent',
                            borderRight: '1px solid var(--border)',
                        }}
                        onClick={() => onSelectTab(tab.path)}>
                        {activeTab === tab.path && (
                            <span className="absolute top-0 left-0 right-0 h-[1px]"
                                style={{ background: 'var(--accent)' }} />
                        )}
                        <span className="text-[14px]">{getFileIcon(tab.name)}</span>
                        <span>
                            {tab.name}
                            {tab.isDiff && <span style={{ color: 'var(--blue)', marginLeft: '4px' }}>(Diff)</span>}
                            {tab.modified && <span style={{ color: 'var(--yellow)' }}> ●</span>}
                        </span>
                        <button onClick={(e) => onCloseTab(tab.path, e)}
                            className="w-4 h-4 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity"
                            style={{ color: 'var(--text-muted)' }}>
                            <FiX size={12} />
                        </button>
                    </div>
                ))}
            </div>

            {/* Editor Content */}
            <div className="flex-1 overflow-hidden relative">
                {!currentTab ? (
                    <WelcomeScreen modelInfo={modelInfo} onOpenChat={onOpenChat} onNewConversation={onNewConversation} />
                ) : (
                    <div className="flex flex-col h-full">
                        {/* File info bar */}
                        <div className="flex items-center gap-3 px-3 py-1 flex-shrink-0"
                            style={{ background: 'var(--bg-dark)', borderBottom: '1px solid var(--border)', fontSize: '12px', zIndex: 10 }}>
                            <span className="px-2 py-0.5 rounded-full text-[11px] font-medium"
                                style={{ background: 'var(--accent-soft)', color: 'var(--accent-bright)' }}>
                                {getLang(currentTab.name)}
                            </span>
                            <span style={{ color: 'var(--text-muted)', flex: 1 }}>{currentTab.path}</span>
                            {currentTab.modified && !currentTab.isDiff && (
                                <>
                                    <span style={{ color: 'var(--yellow)' }}>● Modified</span>
                                    <button onClick={onSave}
                                        className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] transition-all hover:brightness-110"
                                        style={{ background: 'var(--accent-soft)', border: '1px solid var(--accent)', color: 'var(--accent-bright)' }}>
                                        <FiSave size={12} /> Save
                                    </button>
                                </>
                            )}
                            {currentTab.isDiff && (
                                <div className="flex items-center gap-2 ml-auto">
                                    <button onClick={() => onRejectDiff(currentTab.path)}
                                        className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[11px] font-medium transition-all hover:brightness-110"
                                        style={{ background: 'var(--red-soft)', border: '1px solid rgba(248,113,113,0.3)', color: 'var(--red)' }}>
                                        <FiCornerUpLeft size={12} /> Reject Change
                                    </button>
                                    <button onClick={() => onApproveDiff(currentTab.path)}
                                        className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[11px] font-medium transition-all hover:brightness-110"
                                        style={{ background: 'var(--green-glow)', border: '1px solid rgba(74,222,128,0.3)', color: 'var(--green)' }}>
                                        <FiCheck size={12} /> Approve
                                    </button>
                                </div>
                            )}
                        </div>
                        {/* Monaco Editor area */}
                        <div className="flex-1 relative">
                            {currentTab.isDiff ? (
                                <DiffEditor
                                    key={`diff-${currentTab.path}`}
                                    height="100%"
                                    language={getLanguage(currentTab.name)}
                                    theme="light"
                                    original={currentTab.originalDiff || ''}
                                    modified={currentTab.content}
                                    options={{
                                        minimap: { enabled: false },
                                        fontSize: 13,
                                        fontFamily: 'var(--font-mono)',
                                        wordWrap: 'on',
                                        renderSideBySide: true,
                                        ignoreTrimWhitespace: false,
                                        padding: { top: 16 }
                                    }}
                                />
                            ) : (
                                <Editor
                                    key={currentTab.path}
                                    height="100%"
                                    language={getLanguage(currentTab.name)}
                                    theme="light"
                                    value={currentTab.content}
                                    onChange={(value) => onContentChange(currentTab.path, value || '')}
                                    options={{
                                        minimap: { enabled: true },
                                        fontSize: 13,
                                        fontFamily: 'var(--font-mono)',
                                        wordWrap: 'on',
                                        scrollBeyondLastLine: false,
                                        smoothScrolling: true,
                                        padding: { top: 16 },
                                        scrollbar: {
                                            verticalScrollbarSize: 10,
                                            horizontalScrollbarSize: 10,
                                        },
                                    }}
                                />
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

// ── Welcome Screen ──────────────────────────────────────────

function WelcomeScreen({ modelInfo, onOpenChat, onNewConversation }: {
    modelInfo: ModelInfo | null;
    onOpenChat: () => void;
    onNewConversation: () => void;
}) {
    return (
        <div className="flex flex-col items-center justify-center h-full gap-3"
            style={{ color: 'var(--text-muted)', background: 'var(--bg-editor)' }}>
            <h1 className="text-2xl font-semibold tracking-tight"
                style={{ color: 'var(--text-primary)' }}>
                Agentic IDE
            </h1>
            {modelInfo && (
                <span className="text-[12px] opacity-70"
                    style={{ color: 'var(--text-muted)' }}>
                    Powered by {modelInfo.model}
                </span>
            )}
            <div className="flex flex-col gap-2 mt-6">
                {[
                    { key: 'Ctrl+Shift+I', label: 'Toggle AI Chat', action: onOpenChat },
                    { key: 'Ctrl+S', label: 'Save File' },
                    { key: 'Ctrl+N', label: 'New Conversation', action: onNewConversation },
                ].map(({ key, label, action }) => (
                    <div key={key} onClick={action}
                        className="flex items-center gap-4 px-4 py-2 rounded-md cursor-pointer transition-colors"
                        style={{ border: '1px solid transparent' }}
                        onMouseEnter={(e) => e.currentTarget.style.border = '1px solid var(--border)'}
                        onMouseLeave={(e) => e.currentTarget.style.border = '1px solid transparent'}>
                        <kbd className="text-[11px] px-2 py-0.5 rounded text-center min-w-[90px]"
                            style={{ background: 'var(--bg-hover)', color: 'var(--text-secondary)' }}>
                            {key}
                        </kbd>
                        <span className="text-[13px] text-[var(--text-muted)]">{label}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}
