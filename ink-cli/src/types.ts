export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  tools: ToolCall[];
  streaming?: boolean;
}

// Matches backend-emitted status values so ToolBlock UI state stays in
// lockstep with server events (previously 'done' on CLI vs 'completed'
// on web).
export type ToolStatus = 'running' | 'completed' | 'error';

export interface ToolCall {
  tool_id: string;
  tool_name: string;
  status: ToolStatus;
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
  status: 'idle' | 'running' | 'completed' | 'done' | 'error';
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
