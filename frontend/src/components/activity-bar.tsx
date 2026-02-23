'use client';

import { FiFolder, FiSearch, FiTool, FiMessageSquare, FiGitBranch } from 'react-icons/fi';

interface ActivityBarProps {
    activePanel: 'explorer' | 'search' | 'tools' | 'git';
    onPanelChange: (panel: 'explorer' | 'search' | 'tools' | 'git') => void;
    chatOpen: boolean;
    onToggleChat: () => void;
}

const PANELS = [
    { id: 'explorer' as const, icon: <FiFolder size={20} />, title: 'Explorer' },
    { id: 'search' as const, icon: <FiSearch size={20} />, title: 'Search' },
    { id: 'git' as const, icon: <FiGitBranch size={20} />, title: 'Source Control' },
    { id: 'tools' as const, icon: <FiTool size={20} />, title: 'Tools' },
];

export default function ActivityBar({ activePanel, onPanelChange, chatOpen, onToggleChat }: ActivityBarProps) {
    return (
        <div className="w-12 flex-shrink-0 flex flex-col items-center py-3 gap-2"
            style={{ background: 'var(--bg-darkest)', borderRight: '1px solid var(--border)' }}>

            {PANELS.map(({ id, icon, title }) => (
                <button key={id} title={title}
                    className={`w-10 h-10 flex items-center justify-center transition-all duration-300 relative rounded-xl
            ${activePanel === id ? 'text-[var(--text-bright)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]'}`}
                    onClick={() => onPanelChange(id)}>
                    {activePanel === id && (
                        <span className="absolute left-[-4px] top-1/2 -translate-y-1/2 h-5 w-[2px] rounded-r-full bg-[var(--text-primary)]" />
                    )}
                    {icon}
                </button>
            ))}

            <div className="flex-1" />

            <button title="AI Chat (Ctrl+Shift+I)"
                className={`w-10 h-10 mb-2 flex items-center justify-center transition-all duration-300 relative rounded-xl
          ${chatOpen ? 'text-[var(--text-bright)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]'}`}
                onClick={onToggleChat}>
                {chatOpen && (
                    <span className="absolute left-[-4px] top-1/2 -translate-y-1/2 h-5 w-[2px] rounded-r-full bg-[var(--text-primary)]" />
                )}
                <FiMessageSquare size={20} />
            </button>
        </div>
    );
}
