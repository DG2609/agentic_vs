'use client';

import React, { memo } from 'react';
import Markdown from 'react-markdown';
import { FiPlay, FiCopy } from 'react-icons/fi';
import ToolBlock from './tool-block';
import type { ChatMessage } from '@/lib/types';

interface ChatMessageProps {
    msg: ChatMessage;
    isLast: boolean;
    isStreaming: boolean;
    contentBuffer: string;
    mounted: boolean;
    onApplyCode?: (code: string) => void;
}
const cleanContent = (text: string) => {
    if (!text) return '';
    let t = text;
    // Strip markdown JSON block with tool call
    t = t.replace(/```json\s*\{\s*"name"[\s\S]*?\}\s*```/gi, '');
    // Strip raw JSON tool call
    t = t.replace(/\{\s*"name"\s*:\s*"[^"]+"[\s\S]*?"arguments"\s*:[\s\S]*?\}/gi, '');
    // Strip random prefix letters like "ê"
    t = t.replace(/^ê\s*/i, '');
    return t.trim();
};

const ChatMessageItem = memo(({ msg, isLast, isStreaming, contentBuffer, mounted, onApplyCode }: ChatMessageProps) => {
    const displayBuffer = cleanContent(contentBuffer);
    const displayMsg = cleanContent(msg.content);

    const components = {
        code({ node, inline, className, children, ...props }: any) {
            if (inline) {
                return <code className="bg-black/20 px-1 py-0.5 rounded text-[12px]" {...props}>{children}</code>;
            }
            const codeString = String(children).replace(/\n$/, '');
            const match = /language-(\w+)/.exec(className || '');
            return (
                <div className="relative group rounded border border-[var(--border)] overflow-hidden my-3" style={{ background: 'var(--bg-editor)' }}>
                    <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--border)] text-[10px] text-[var(--text-muted)] group-hover:text-[var(--text-primary)] transition-colors" style={{ background: 'var(--bg-panel)' }}>
                        <span className="uppercase font-medium tracking-wider">{match ? match[1] : 'code'}</span>
                        <div className="flex gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                            {onApplyCode && (
                                <button onClick={() => onApplyCode(codeString)} className="hover:text-amber-500 font-medium flex items-center gap-1 transition-colors">
                                    <FiPlay /> Apply to Editor
                                </button>
                            )}
                            <button onClick={(e) => {
                                navigator.clipboard.writeText(codeString);
                                const el = e.currentTarget;
                                el.classList.add('text-green-500');
                                setTimeout(() => el.classList.remove('text-green-500'), 2000);
                            }} className="hover:text-[var(--text-primary)] flex items-center gap-1 transition-colors">
                                <FiCopy className="transition-colors" /> Copy
                            </button>
                        </div>
                    </div>
                    <div className="p-3 overflow-x-auto text-[12px] font-mono leading-relaxed scrollbar-thin scrollbar-thumb-gray-800 scrollbar-track-transparent">
                        <code className={className} {...props}>
                            {children}
                        </code>
                    </div>
                </div>
            );
        }
    };
    return (
        <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
            {/* Tool blocks */}
            {msg.tools.map((tool) => (
                <ToolBlock key={tool.tool_id} tool={tool} />
            ))}
            {/* Message content */}
            {msg.role === 'user' ? (
                <div className="px-3 py-2 text-[13px] leading-relaxed font-medium mb-1 rounded-sm"
                    style={{ background: 'var(--bg-input)', borderLeft: '2px solid var(--accent)', color: 'var(--text-bright)' }}>
                    {msg.content}
                </div>
            ) : (
                <div className="text-[13px] leading-[1.6] prose prose-invert prose-sm max-w-none break-words tracking-wide" style={{ color: 'var(--text-primary)' }}>
                    {isLast && isStreaming ? (
                        displayBuffer ? (
                            <Markdown components={components}>{displayBuffer}</Markdown>
                        ) : (
                            <div className="flex gap-1.5 py-2">
                                {[0, 1, 2].map((n) => (
                                    <span key={n} className="w-1.5 h-1.5 rounded-full"
                                        style={{ background: 'var(--accent)', animation: `bounce 1.2s infinite ${n * 0.15}s` }} />
                                ))}
                            </div>
                        )
                    ) : displayMsg ? (
                        <Markdown components={components}>{displayMsg}</Markdown>
                    ) : null}
                </div>
            )}
            {/* Timestamp */}
            <div className="text-[10px] mt-1 opacity-0 hover:opacity-100 transition-opacity"
                style={{ color: 'var(--text-muted)' }}>
                {mounted ? msg.timestamp.toLocaleTimeString() : ''}
            </div>
        </div>
    );
});

ChatMessageItem.displayName = 'ChatMessageItem';

export default ChatMessageItem;
