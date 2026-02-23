'use client';

import { useRef, useEffect, useState } from 'react';
import { FiPlus, FiX, FiSend, FiSquare, FiCpu } from 'react-icons/fi';
import ChatMessageItem from './chat-message';
import type { ChatMessage, ModelInfo } from '@/lib/types';

interface ChatPanelProps {
    messages: ChatMessage[];
    inputValue: string;
    onInputChange: (value: string) => void;
    onSend: () => void;
    onStop: () => void;
    onNewConversation: () => void;
    onClose: () => void;
    isStreaming: boolean;
    connected: boolean;
    modelInfo: ModelInfo | null;
    contentBuffer: string;
    onApplyCode?: (code: string) => void;
    mode?: 'chat' | 'plan' | 'code';
    onModeChange?: (mode: 'chat' | 'plan' | 'code') => void;
}

export default function ChatPanel({
    messages, inputValue, onInputChange, onSend, onStop,
    onNewConversation, onClose, isStreaming, connected, modelInfo, contentBuffer,
    onApplyCode, mode = 'code', onModeChange
}: ChatPanelProps) {
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, contentBuffer]);

    return (
        <div className="flex flex-col flex-shrink-0 transition-all font-sans"
            style={{ width: '400px', background: 'var(--bg-panel)', borderLeft: '1px solid var(--border)' }}>

            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
                <div className="flex items-center gap-2 text-[12px] font-medium" style={{ color: 'var(--text-primary)' }}>
                    <span>AI Chat</span>
                    <span className={`w-1.5 h-1.5 rounded-full transition-all`}
                        style={{ background: connected ? 'var(--text-muted)' : 'var(--red)' }} />
                </div>
                <div className="flex items-center gap-1">
                    <button onClick={onNewConversation} title="New Conversation (Ctrl+N)"
                        className="w-7 h-7 rounded-md flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-all">
                        <FiPlus size={16} />
                    </button>
                    <button onClick={onClose} title="Close (Ctrl+Shift+I)"
                        className="w-7 h-7 rounded-md flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-all">
                        <FiX size={14} />
                    </button>
                </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-3 py-3 scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent">
                <div className="flex flex-col gap-4">
                    {messages.map((msg, i) => (
                        <ChatMessageItem
                            key={i}
                            msg={msg}
                            isLast={i === messages.length - 1}
                            isStreaming={isStreaming}
                            contentBuffer={contentBuffer}
                            mounted={mounted}
                            onApplyCode={onApplyCode}
                        />
                    ))}
                    <div ref={messagesEndRef} />
                </div>
            </div>

            {/* Input Area */}
            <div className="px-4 pb-4 pt-2 flex-shrink-0 relative">
                {isStreaming && (
                    <button onClick={onStop}
                        className="w-full flex items-center justify-center gap-1.5 py-1.5 mb-2 rounded-md text-[12px] font-medium transition-all hover:bg-[var(--bg-hover)]"
                        style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                        <FiSquare size={12} /> Stop generating
                    </button>
                )}

                {/* Mode Switcher */}
                {onModeChange && (
                    <div className="flex gap-2 mb-2 text-[11px] font-medium">
                        <button
                            onClick={() => onModeChange('chat')}
                            className={`px-3 py-1 rounded-full transition-all border ${mode === 'chat' ? 'bg-[var(--text-primary)] text-[var(--bg-panel)] border-transparent' : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
                            Chat
                        </button>
                        <button
                            onClick={() => onModeChange('plan')}
                            className={`px-3 py-1 rounded-full transition-all border ${mode === 'plan' ? 'bg-[var(--text-primary)] text-[var(--bg-panel)] border-transparent' : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
                            Plan
                        </button>
                        <button
                            onClick={() => onModeChange('code')}
                            className={`px-3 py-1 rounded-full transition-all border ${mode === 'code' ? 'bg-[var(--accent)] text-white border-transparent' : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
                            Act / Code
                        </button>
                    </div>
                )}

                <div className="flex items-end gap-2 rounded-lg px-3 py-2.5 transition-all"
                    style={{ background: 'var(--bg-editor)', border: '1px solid var(--border)' }}>
                    <textarea ref={textareaRef}
                        value={inputValue}
                        onChange={(e) => {
                            onInputChange(e.target.value);
                            e.target.style.height = 'auto';
                            e.target.style.height = Math.min(e.target.scrollHeight, 150) + 'px';
                        }}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                onSend();
                            }
                        }}
                        placeholder={connected ? "Ask anything..." : "Connecting..."}
                        disabled={!connected}
                        rows={1}
                        className="flex-1 resize-none outline-none text-[13px] leading-relaxed max-h-[150px] disabled:opacity-50"
                        style={{ background: 'none', color: 'var(--text-primary)', fontFamily: 'var(--font-sans)' }} />
                    <button onClick={onSend} disabled={isStreaming || !inputValue.trim() || !connected}
                        className="w-7 h-7 rounded-md flex items-center justify-center transition-all flex-shrink-0 disabled:opacity-30 disabled:cursor-not-allowed hover:opacity-90 active:scale-95"
                        style={{ background: 'var(--text-primary)', color: 'var(--bg-editor)' }}>
                        <FiSend size={14} />
                    </button>
                </div>
                <div className="flex justify-between items-center mt-2 px-1">
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        {modelInfo ? `${modelInfo.model}` : ''}
                    </div>
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        Return to send
                    </div>
                </div>
            </div>
        </div>
    );
}
