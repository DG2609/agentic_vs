'use client';

import type { ModelInfo } from '@/lib/types';

interface StatusBarProps {
    connected: boolean;
    modelInfo: ModelInfo | null;
    toolCount?: number;
}

export default function StatusBar({ connected, modelInfo, toolCount = 27 }: StatusBarProps) {
    return (
        <div className="h-6 flex items-center justify-between px-3 text-[10px] uppercase tracking-wider font-semibold flex-shrink-0 z-50 select-none"
            style={{ background: 'var(--bg-darkest)', borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}>
            <div className="flex items-center gap-4">
                <span className="flex items-center gap-1.5" title={connected ? 'Connected to Agentic Backend' : 'Waiting for connection...'}>
                    <span className={`w-1.5 h-1.5 rounded-full transition-all ${connected ? 'shadow-[0_0_6px_var(--green-glow)]' : ''}`}
                        style={{ background: connected ? 'var(--green)' : 'var(--yellow)', animation: connected ? 'none' : 'shadow-pulse 2s infinite' }} />
                    <span style={{ color: connected ? 'var(--green)' : 'var(--yellow)' }}>
                        {connected ? 'CONNECTED' : 'DISCONNECTED'}
                    </span>
                </span>
                {modelInfo && (
                    <span className="flex items-center gap-1.5 opacity-80 hover:opacity-100 transition-opacity">
                        <span className="text-[12px]">⚡</span>
                        <span style={{ color: 'var(--accent-bright)', fontFamily: 'var(--font-mono)' }}>{modelInfo.model}</span>
                    </span>
                )}
            </div>
            <div className="flex items-center gap-4 opacity-80 hover:opacity-100 transition-opacity">
                <span className="flex items-center gap-1">
                    <span className="text-[12px]">🔧</span>
                    <span style={{ fontFamily: 'var(--font-mono)' }}>{toolCount} TOOLS</span>
                </span>
                {modelInfo && <span className="uppercase" style={{ fontFamily: 'var(--font-mono)' }}>{modelInfo.provider}</span>}
            </div>
        </div>
    );
}
