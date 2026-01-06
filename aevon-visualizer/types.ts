
export enum AgentType {
  CLAUDE = "claude",
  GEMINI = "gemini",
  OLLAMA = "ollama",
  AUTO = "auto"
}

export enum RunStatus {
  IDLE = "IDLE",
  RUNNING = "RUNNING",
  PAUSED = "PAUSED",
  COMPLETED = "COMPLETED",
  FAILED = "FAILED"
}

export interface OrchestratorConfig {
  agent: AgentType;
  maxIterations: number;
  maxRuntime: number;
  maxCost: number;
  maxTokens: number;
  retryDelay: number;
  checkpointInterval: number;
  prompt: string;
}

export interface IterationLog {
  id: string;
  iteration: number;
  timestamp: number;
  tokens: number;
  cost: number;
  status: 'success' | 'retry' | 'checkpoint';
  message: string;
}

export interface OrchestratorState {
  status: RunStatus;
  currentIteration: number;
  totalIterations: number;
  elapsedTime: number; // in seconds
  totalTokens: number;
  totalCost: number;
  logs: IterationLog[];
}

export interface VisualizerIteration {
  iteration: number;
  timestamp?: string;
  tokens?: number;
  cost?: number;
  status?: string;
  message?: string;
}

export interface VisualizerSnapshot {
  run_id?: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
  current_iteration?: number;
  total_iterations?: number;
  total_tokens?: number;
  total_cost?: number;
  elapsed_seconds?: number;
  iterations?: VisualizerIteration[];
}
