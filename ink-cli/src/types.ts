export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  tools: ToolCall[];
  streaming?: boolean;
}

export interface ToolCall {
  tool_id: string;
  tool_name: string;
  status: 'running' | 'done' | 'error';
  input?: Record<string, unknown>;
  output?: string;
  elapsed_ms?: number;
  expanded: boolean;
}

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
}

export interface WorkerInfo {
  id: string;
  role: string;
  status: 'idle' | 'running' | 'done' | 'error';
}

export interface AppConfig {
  provider: string;
  model: string;
}

export interface ServerState {
  isReady: boolean;
  isStarting: boolean;
  error: string | null;
  pid: number | null;
}
