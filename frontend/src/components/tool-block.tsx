'use client';

import { useState } from 'react';
import { FiChevronRight } from 'react-icons/fi';
import { TOOL_ICONS } from '@/lib/constants';
import type { ToolBlock as ToolBlockType } from '@/lib/types';

interface ToolBlockProps {
    tool: ToolBlockType;
}

export default function ToolBlock({ tool }: ToolBlockProps) {
    const [expanded, setExpanded] = useState(false);
    const icon = TOOL_ICONS[tool.tool_name] || '🔧';

    return (
        <div className="my-1.5 rounded-md overflow-hidden text-[12px] transition-all"
            style={{
                border: `1px solid var(--border)`,
                background: 'var(--bg-editor)'
            }}>
            {/* Header */}
            <div className="flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-all relative overflow-hidden bg-[var(--bg-panel)] hover:bg-[var(--bg-hover)]"
                style={{
                    color: tool.status === 'running' ? 'var(--text-primary)' : tool.status === 'error' ? 'var(--red)' : 'var(--text-secondary)',
                }}
                onClick={() => setExpanded(!expanded)}>

                {/* Status icon */}
                {tool.status === 'running' ? (
                    <div className="w-3.5 h-3.5 rounded-full border-[1.5px] border-b-transparent flex-shrink-0"
                        style={{ borderTopColor: 'var(--text-primary)', borderRightColor: 'var(--text-primary)', borderLeftColor: 'var(--text-primary)', animation: 'spin 0.6s linear infinite' }} />
                ) : tool.status === 'completed' ? (
                    <span className="text-[14px] flex-shrink-0" style={{ color: 'var(--text-muted)' }}>✓</span>
                ) : (
                    <span className="text-[14px] flex-shrink-0" style={{ color: 'var(--red)' }}>✗</span>
                )}

                <span className="text-[13px] flex-shrink-0 opacity-80">{icon}</span>
                <span className="flex-1 font-medium tracking-wide">{tool.tool_name}</span>
                <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                    {tool.status === 'running' ? 'Running...' : tool.status === 'completed' ? 'Done' : 'Error'}
                </span>
                <FiChevronRight size={10} className={`transition-transform ${expanded ? 'rotate-90' : ''}`}
                    style={{ color: 'var(--text-muted)' }} />
            </div>

            {/* Expanded body */}
            {expanded && (
                <div className="overflow-y-auto" style={{ maxHeight: '250px', background: 'var(--bg-editor)' }}>
                    <div className="px-3 py-2.5 text-[11px] leading-relaxed whitespace-pre-wrap break-words"
                        style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                        {tool.arguments && (
                            <details className="pb-2 mb-2 cursor-pointer group" style={{ borderBottom: '1px solid var(--border)' }}>
                                <summary className="text-[10px] font-semibold tracking-wider outline-none text-[var(--text-muted)] group-hover:text-[var(--text-primary)] transition-colors">
                                    ARGUMENTS (Click to expand)
                                </summary>
                                <pre className="mt-2 text-[10.5px] opacity-90">{JSON.stringify(tool.arguments, null, 2)}</pre>
                            </details>
                        )}
                        {tool.result && (
                            <div className="mt-3">
                                <span className="text-[10px] font-semibold tracking-wider" style={{ color: 'var(--text-muted)' }}>RESULT</span>
                                {tool.tool_name === 'terminal_exec' ? (
                                    <div className="mt-1.5 p-2 rounded-md overflow-x-auto"
                                        style={{
                                            background: tool.result.includes('✅ Exit code:') ? 'rgba(74,222,128,0.05)' : 'rgba(248,113,113,0.05)',
                                            border: tool.result.includes('✅ Exit code:') ? '1px solid rgba(74,222,128,0.2)' : '1px solid rgba(248,113,113,0.2)'
                                        }}>
                                        <pre style={{
                                            color: tool.result.includes('✅ Exit code:') ? 'var(--green)' : 'var(--red)',
                                            textShadow: '0 0 10px rgba(0,0,0,0.5)'
                                        }}>{tool.result}</pre>
                                    </div>
                                ) : (
                                    <pre className="mt-1" style={{ color: 'var(--text-secondary)' }}>{tool.result}</pre>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
