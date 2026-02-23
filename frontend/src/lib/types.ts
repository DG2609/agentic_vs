export interface FileItem {
    name: string;
    path: string;
    is_dir: boolean;
    size?: number;
}

export interface OpenTab {
    path: string;
    name: string;
    content: string;
    original: string;
    modified: boolean;
    isDiff?: boolean;
    originalDiff?: string;
}

export interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    tools: ToolBlock[];
}

export interface ToolBlock {
    tool_id: string;
    tool_name: string;
    status: 'running' | 'completed' | 'error';
    arguments?: Record<string, unknown>;
    result?: string;
}

export interface ModelInfo {
    provider: string;
    model: string;
}
